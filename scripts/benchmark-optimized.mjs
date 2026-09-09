import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import {
  optionsFromArgs,
  safeDatasetPath,
  validateDataset,
  salaryKey,
  lexicalCoverage,
} from './benchmark-lib.mjs';
import {
  loadEngine,
  extractPart,
  extractionParts,
  hash,
} from './extraction-engine.mjs';

async function main() {
  const options = optionsFromArgs(process.argv.slice(2));
  if (options.help) {
    console.log(
      'npm run benchmark -- [--dry-run] [--dataset PATH] [--mode both|salary|keywords] [--limit N] [--concurrency 4] [--max-tokens 4096] [--timeout 300] [--model LOADED_ID]\nUses final v7 source-grounded profile with the currently loaded model. The earlier harness is npm run benchmark:legacy. Reports stay in .experiment/benchmarks/.',
    );
    return;
  }
  if (options.reasoning !== 'auto')
    throw new Error(
      'Optimized profile uses capability-aware auto reasoning; legacy experiments support explicit overrides',
    );
  process.chdir(fileURLToPath(new URL('../', import.meta.url)));
  const input = safeDatasetPath(
    await fs.realpath(safeDatasetPath(options.dataset)),
  );
  if ((await fs.stat(input)).size > 20 * 1024 * 1024)
    throw new Error('Dataset too large');
  const bytes = await fs.readFile(input, 'utf8');
  let samples;
  try {
    samples = validateDataset(JSON.parse(bytes)).slice(0, options.limit);
  } catch {
    throw new Error('Invalid private dataset');
  }
  const engine = await loadEngine({
    maxTokens: options.maxTokens,
    timeout: options.timeout,
    concurrency: options.concurrency,
    strictModel: false,
    modelId: options.model ?? undefined,
  });
  const tasks = samples.flatMap((sample) =>
    extractionParts(sample.fields, options.mode).map((t) => ({ ...t, sample })),
  );
  const concurrency = Math.min(
    options.concurrency,
    engine.configuration.model.config.parallel ?? 1,
  );
  console.log(
    `Model ${engine.configuration.model.id}; profile v7; reasoning ${engine.configuration.reasoning ?? 'server default (unknown)'}; context ${engine.configuration.model.config.context_length}; concurrency ${concurrency}; ${tasks.length} planned calls.`,
  );
  if (options.dryRun) {
    console.log('Dry run: saved inputs checked, no generation.');
    return;
  }
  const privateRoot = path.resolve('.experiment');
  await fs.mkdir(privateRoot, { recursive: true });
  if (
    (await fs.realpath(privateRoot)) !== privateRoot ||
    spawnSync('git', ['check-ignore', '-q', '.experiment/benchmarks'])
      .status !== 0
  )
    throw new Error('Output must be private and Git-ignored');
  const reports = path.join(privateRoot, 'benchmarks');
  await fs.mkdir(reports, { recursive: true });
  if ((await fs.realpath(reports)) !== reports)
    throw new Error('Output must not be a symlink');
  const out = path.join(
    reports,
    `${new Date().toISOString().replaceAll(':', '-')}-${randomUUID().slice(0, 8)}`,
  );
  await fs.mkdir(out);
  const results = [];
  let stopping = false,
    next = 0;
  const begin = performance.now();
  process.on('SIGINT', () => {
    stopping = true;
    console.log('Stopping after in-flight calls.');
  });
  async function worker() {
    while (!stopping && next < tasks.length) {
      const task = tasks[next++];
      let result;
      try {
        result = await extractPart(
          engine,
          task.sample.fields,
          task.mode,
          task.part,
        );
      } catch {
        stopping = true;
        result = {
          status: 'failed',
          accepted: [],
          rejected: ['infrastructure'],
        };
      }
      const row = {
        id: task.sample.id,
        mode: task.mode,
        part: task.part,
        ...result,
      };
      results.push(row);
      await fs.writeFile(
        path.join(out, `${row.id}-${row.mode}-${row.part}.json`),
        JSON.stringify(row, null, 2),
      );
      console.log(`${row.id} ${row.mode}/${row.part}: ${row.status}`);
    }
  }
  await Promise.allSettled(Array.from({ length: concurrency }, () => worker()));
  const salaries = results.filter((r) => r.mode === 'salary');
  const salaryMatches = salaries.filter((r) => {
    if (r.status !== 'accepted') return false;
    const sample = samples.find((s) => s.id === r.id);
    return (
      JSON.stringify(
        r.accepted
          .flatMap((q) =>
            q.rates.map((v) =>
              salaryKey([v.currency, v.minimum, v.maximum, v.period]),
            ),
          )
          .sort(),
      ) === JSON.stringify(sample.reference.salary.map(salaryKey).sort())
    );
  }).length;
  const keywordCases = samples
    .filter((s) => results.some((r) => r.id === s.id && r.mode === 'keywords'))
    .map((s) => ({
      id: s.id,
      ...lexicalCoverage(
        results
          .filter((r) => r.id === s.id && r.mode === 'keywords')
          .flatMap((r) => r.accepted.map((t) => t.text)),
        s.reference.keywords,
      ),
    }));
  const keywordReplies = results.filter((r) => r.mode === 'keywords');
  const sum = (a, f) => a.reduce((n, r) => n + f(r), 0);
  const table = [
    {
      Metric: 'Salary amounts/currency/period match, whole posting',
      Result: `${salaryMatches}/${salaries.length}`,
    },
    {
      Metric: 'Salary reply JSON parses',
      Result: `${sum(salaries, (r) => Number(Boolean(r.jsonValid)))}/${salaries.length}`,
    },
    {
      Metric: 'Keyword reply JSON parses (whole + field passes)',
      Result: `${sum(keywordReplies, (r) => Number(Boolean(r.jsonValid)))}/${keywordReplies.length}`,
    },
    {
      Metric: 'Keyword reference-name/alias coverage, deduplicated',
      Result: `${sum(keywordCases, (r) => r.matched)}/${sum(keywordCases, (r) => r.total)}`,
    },
    {
      Metric: 'Rejected source/output entries',
      Result: String(sum(results, (r) => r.rejected.length)),
    },
    {
      Metric: 'Wall time',
      Result: `${((performance.now() - begin) / 1000).toFixed(1)}s`,
    },
  ];
  const summary = {
    configuration: engine.configuration,
    configurationHash: engine.configurationHash,
    datasetHash: hash(bytes),
    planned: tasks.length,
    completed: results.length,
    stopped: stopping,
    table,
    keywordCases,
    results,
  };
  await fs.writeFile(
    path.join(out, 'results.json'),
    JSON.stringify(summary, null, 2),
  );
  await fs.writeFile(
    path.join(out, 'report.md'),
    `# Optimized extraction benchmark\n\nModel: ${engine.configuration.model.id}\nCompleted calls: ${results.length}/${tasks.length}\n\n| Metric | Result |\n|---|---:|\n${table.map((r) => `| ${r.Metric} | ${r.Result} |`).join('\n')}\n\nLexical anchors are not exhaustive semantic recall. Exact source spans are not a full semantic or eligibility audit. This is not the legacy Python production validator. Failed calls supply no terms; partial runs are not comparable to complete runs.\n`,
  );
  console.table(table);
  console.log(
    `Report: ${path.relative(process.cwd(), path.join(out, 'report.md'))}`,
  );
  if (
    stopping ||
    results.length !== tasks.length ||
    results.some((r) => r.status === 'failed' || r.status === 'rejected')
  )
    process.exitCode = 2;
}
main().catch(() => {
  console.error(
    'Benchmark stopped. Check options, private dataset and loaded model. Completed per-call files remain private.',
  );
  process.exitCode = 1;
});
