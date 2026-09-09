import { DatabaseSync } from 'node:sqlite';
import { randomInt, randomUUID, createHash } from 'node:crypto';
import { mkdirSync, writeFileSync, lstatSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { cpus, platform } from 'node:os';
import { performance } from 'node:perf_hooks';
import {
  prepareKeywordProfile,
  scoreKeywordCoverage,
  prepareKeywordSearch,
  scoreKeywordCoverageBatch,
  KEYWORD_COVERAGE_VERSION,
} from '../frontend/src/lib/keywordCoverage.ts';

// Fixed local read-only input and ignored output; no network, refresh or inference.
if (process.argv.length !== 2)
  throw new Error('This benchmark takes no arguments.');
const outputRoot = resolve('.experiment');
if (lstatSync(outputRoot).isSymbolicLink())
  throw new Error('Private output root must not be a symlink.');
execFileSync('git', [
  'check-ignore',
  '-q',
  '--',
  '.experiment/keyword-coverage/',
]);
const databasePath = resolve('data/waterlooworks.sqlite3');
if (lstatSync(databasePath).isSymbolicLink())
  throw new Error('Database must not be a symlink.');
const repetitions = 50,
  targetsCount = 100;
const started = performance.now();
const db = new DatabaseSync(databasePath, { readOnly: true });
db.exec('PRAGMA query_only=ON; PRAGMA busy_timeout=5000; BEGIN;');
let run, population, snapshots, selection;
try {
  run = db
    .prepare(
      'SELECT run_id,status,configuration_hash FROM source_extraction_runs ORDER BY created_at DESC LIMIT 1',
    )
    .get();
  if (!run) throw new Error('No saved extraction run available.');
  population = db
    .prepare(
      `SELECT i.posting_id FROM source_extraction_items i
    JOIN posting_contents c ON c.posting_id=i.posting_id
    WHERE i.run_id=? AND i.mode='keywords'
    GROUP BY i.posting_id HAVING SUM(i.status NOT IN ('accepted','partial','rejected','failed'))=0
    AND MIN(i.content_hash=c.content_hash)=1 ORDER BY i.posting_id`,
    )
    .all(run.run_id)
    .map((r) => r.posting_id);
  if (population.length < targetsCount + 1)
    throw new Error('Need101 current processed postings.');
  selection = [...population];
  for (let i = 0; i <= targetsCount; i++) {
    const j = randomInt(i, selection.length);
    [selection[i], selection[j]] = [selection[j], selection[i]];
  }
  selection = selection.slice(0, targetsCount + 1);
  const source = db.prepare(`SELECT content_hash,content_json,
    (SELECT title FROM postings p WHERE p.posting_id=c.posting_id) title
    FROM posting_contents c WHERE posting_id=?`);
  const terms =
    db.prepare(`SELECT DISTINCT t.name FROM source_extraction_items i
    JOIN source_extractions_current e ON e.extraction_id=i.extraction_id
    JOIN source_extraction_terms t USING(extraction_id)
    WHERE i.run_id=? AND i.posting_id=? AND i.mode='keywords' ORDER BY t.name`);
  snapshots = selection.map((id) => ({
    posting_id: id,
    ...source.get(id),
    keywords: terms.all(run.run_id, id).map((t) => t.name),
  }));
  db.exec('COMMIT');
} catch (error) {
  db.exec('ROLLBACK');
  throw error;
} finally {
  db.close();
}
const readMs = performance.now() - started;
const fields = snapshots.map((s) =>
  Object.values(JSON.parse(s.content_json).fields).filter(
    (v) => typeof v === 'string',
  ),
);
const text = fields[0].join('\n\n');
const begin = performance.now();
const profiles = snapshots
  .slice(1)
  .map((s, index) => prepareKeywordProfile(fields[index + 1], s.keywords));
const preparationMs = performance.now() - begin;
const indexStart = performance.now();
const search = prepareKeywordSearch(profiles);
const indexMs = performance.now() - indexStart;
const scoreAll = () => scoreKeywordCoverageBatch(text, search);
const firstStart = performance.now();
const scores = scoreAll();
const firstBatchMs = performance.now() - firstStart;
const selfProfile = prepareKeywordProfile(fields[0], snapshots[0].keywords);
const self = scoreKeywordCoverage(text, selfProfile);
if (self.score !== null && Math.abs(self.score - 100) > 1e-9)
  throw new Error('Self comparison invariant failed.');
// Real work, including explanations, not mocked timing or an empty loop.
for (let i = 0; i < 5; i++) scoreAll();
const samples = [];
let checksum = 0;
for (let i = 0; i < repetitions; i++) {
  const start = performance.now();
  const batch = scoreAll();
  samples.push(performance.now() - start);
  checksum += batch.reduce((sum, result) => sum + (result.score ?? 0), 0);
}
const sorted = [...samples].sort((a, b) => a - b);
const percentile = (p) => sorted[Math.ceil(p * sorted.length) - 1];
const summary = {
  algorithm: KEYWORD_COVERAGE_VERSION,
  timestamp: new Date().toISOString(),
  node: process.version,
  platform: platform(),
  cpu: cpus()[0]?.model,
  run,
  population: population.length,
  sampling:
    'crypto.randomInt partial Fisher-Yates; uniform without replacement; first draw is source, next100 distinct targets; no resampling',
  source_id: selection[0],
  source_characters: [...text].length,
  targets: targetsCount,
  total_unique_indexed_names: snapshots
    .slice(1)
    .reduce((n, s) => n + s.keywords.length, 0),
  weighted_keywords: profiles.reduce((n, p) => n + p.terms.length, 0),
  unavailable_targets: scores.filter((s) => s.score === null).length,
  self_score: self.score,
  timings_ms: {
    database_read_and_snapshot: readMs,
    prepare_100_profiles: preparationMs,
    prepare_shared_matchers: indexMs,
    first_score_100: firstBatchMs,
    first_prepare_and_score_100: preparationMs + indexMs + firstBatchMs,
    first_read_prepare_score_100:
      readMs + preparationMs + indexMs + firstBatchMs,
    warm_batch_median: percentile(0.5),
    warm_batch_p95: percentile(0.95),
    warm_batch_min: sorted[0],
    warm_batch_max: sorted.at(-1),
    warm_mean_per_comparison:
      samples.reduce((n, v) => n + v, 0) / repetitions / targetsCount,
  },
  repetitions,
  checksum,
  code_sha256: createHash('sha256')
    .update(
      readFileSync(
        new URL('../frontend/src/lib/keywordCoverage.ts', import.meta.url),
      ),
    )
    .digest('hex'),
};
const ranked = scores
  .map((score, index) => ({
    posting_id: selection[index + 1],
    title: snapshots[index + 1].title,
    ...score,
  }))
  .sort(
    (a, b) =>
      (b.score ?? -1) - (a.score ?? -1) ||
      a.posting_id.localeCompare(b.posting_id, 'en', { numeric: true }),
  );
const dir = resolve(
  outputRoot,
  `keyword-coverage-${Date.now()}-${randomUUID()}`,
);
mkdirSync(dir);
const snapshot = JSON.stringify({
  eligible_ids: population,
  selection,
  snapshots,
});
summary.snapshot_sha256 = createHash('sha256').update(snapshot).digest('hex');
writeFileSync(resolve(dir, 'snapshot.json'), snapshot);
writeFileSync(
  resolve(dir, 'results.json'),
  JSON.stringify({ summary, ranked, batch_timings_ms: samples }, null, 2),
);
console.table(summary.timings_ms);
console.log(
  JSON.stringify({
    output: dir,
    source_id: summary.source_id,
    targets: targetsCount,
    source_characters: summary.source_characters,
    self_score: summary.self_score,
    unavailable_targets: summary.unavailable_targets,
  }),
);
