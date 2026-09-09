import { randomUUID } from 'node:crypto';
import { countryInput, hash } from './country-lib.mjs';
import { transaction, assertIdleSource } from './extraction-store.mjs';

const now = () => new Date().toISOString();
export const sourceQuery = `SELECT p.posting_id,p.listing_json,p.content_hash listing_hash,c.content_json,c.content_hash,
 (SELECT max(revision_id) FROM posting_revisions r WHERE r.posting_id=p.posting_id AND r.content_hash=p.content_hash) listing_revision_id,
 (SELECT max(revision_id) FROM posting_content_revisions r WHERE r.posting_id=p.posting_id AND r.content_hash=c.content_hash) content_revision_id
 FROM postings p LEFT JOIN posting_contents c USING(posting_id)`;
export const inputHash = (source) =>
  hash(JSON.stringify([source.listing_hash, source.content_hash]));
export function inputFor(source) {
  return countryInput(
    JSON.parse(source.listing_json),
    source.content_json ? JSON.parse(source.content_json).fields : {},
  );
}
function idle(db) {
  assertIdleSource(db);
  if (
    db
      .prepare(
        "SELECT 1 FROM source_extraction_runs WHERE status='running' AND lease_until_ms>=?",
      )
      .get(Date.now())
  )
    throw new Error('source_busy');
}
export function ownerRun(db, runId, owner) {
  const run = db
    .prepare('SELECT * FROM country_inference_runs WHERE run_id=?')
    .get(runId);
  if (
    !run ||
    run.owner !== owner ||
    run.status !== 'running' ||
    run.lease_until_ms < Date.now()
  )
    throw new Error('lease_lost');
  return run;
}
export function beginCountryRun(
  db,
  engine,
  { runId = null, ids = [], limit = 10000 } = {},
) {
  return transaction(db, () => {
    idle(db);
    if (
      db
        .prepare(
          "SELECT 1 FROM country_inference_runs WHERE status='running' AND lease_until_ms>=?",
        )
        .get(Date.now())
    )
      throw new Error('country_busy');
    db.prepare(
      "UPDATE country_inference_runs SET status='stopped',owner=NULL WHERE status='running' AND lease_until_ms<?",
    ).run(Date.now());
    const owner = randomUUID();
    if (runId) {
      const old = db
        .prepare('SELECT * FROM country_inference_runs WHERE run_id=?')
        .get(runId);
      if (
        !old ||
        old.configuration_hash !== engine.configurationHash ||
        old.status === 'completed'
      )
        throw new Error('resume_identity');
      // A lost in-flight request may already have completed at the provider. Never silently reissue it.
      db.prepare(
        "UPDATE country_inference_items SET status='failed',reason='interrupted' WHERE run_id=? AND status='running'",
      ).run(runId);
      db.prepare(
        "UPDATE country_inference_runs SET status='running',owner=?,lease_until_ms=?,stop_requested=0,updated_at=? WHERE run_id=?",
      ).run(owner, Date.now() + 120000, now(), runId);
      return { runId, owner };
    }
    runId = randomUUID();
    db.prepare(
      "INSERT INTO country_inference_runs VALUES(?,?,?,'running',?,?,0,?,?)",
    ).run(
      runId,
      engine.configurationHash,
      JSON.stringify(engine.configuration),
      owner,
      Date.now() + 120000,
      now(),
      now(),
    );
    const sources = db
      .prepare(
        sourceQuery + ' ORDER BY CAST(p.posting_id AS INTEGER),p.posting_id',
      )
      .all()
      .filter((s) => !ids.length || ids.includes(s.posting_id))
      .slice(0, limit);
    if (ids.some((id) => !sources.some((s) => s.posting_id === id)))
      throw new Error('missing_source');
    for (const source of sources) {
      const digest = inputHash(source);
      const cached = db
        .prepare(
          `SELECT i.* FROM country_inference_items i JOIN country_inference_runs r USING(run_id)
        WHERE i.posting_id=? AND i.listing_hash=? AND i.content_hash IS ? AND i.input_hash=? AND r.configuration_hash=?
        AND i.status IN ('accepted','unknown') ORDER BY r.created_at DESC,r.run_id DESC LIMIT 1`,
        )
        .get(
          source.posting_id,
          source.listing_hash,
          source.content_hash,
          digest,
          engine.configurationHash,
        );
      db.prepare(
        `INSERT INTO country_inference_items(run_id,posting_id,listing_revision_id,content_revision_id,listing_hash,content_hash,input_hash,status,country,confidence,reason,evidence_json,origin_run_id)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)`,
      ).run(
        runId,
        source.posting_id,
        source.listing_revision_id,
        source.content_revision_id,
        source.listing_hash,
        source.content_hash,
        digest,
        cached?.status ?? 'pending',
        cached?.country ?? null,
        cached?.confidence ?? null,
        cached?.reason ?? null,
        cached?.evidence_json ?? '[]',
        cached ? (cached.origin_run_id ?? cached.run_id) : null,
      );
    }
    return { runId, owner };
  });
}
export function claimCountry(db, runId, owner) {
  return transaction(db, () => {
    if (ownerRun(db, runId, owner).stop_requested) return null;
    idle(db);
    const item = db
      .prepare(
        "SELECT * FROM country_inference_items WHERE run_id=? AND status='pending' ORDER BY CAST(posting_id AS INTEGER),posting_id LIMIT 1",
      )
      .get(runId);
    if (!item) return null;
    const source = db
      .prepare(sourceQuery + ' WHERE p.posting_id=?')
      .get(item.posting_id);
    if (!source || inputHash(source) !== item.input_hash) {
      db.prepare(
        "UPDATE country_inference_items SET status='stale' WHERE run_id=? AND posting_id=?",
      ).run(runId, item.posting_id);
      return { stale: true };
    }
    db.prepare(
      "UPDATE country_inference_items SET status='running',attempts=1 WHERE run_id=? AND posting_id=?",
    ).run(runId, item.posting_id);
    return { ...item, input: inputFor(source) };
  });
}
export function persistCountry(db, runId, owner, item, result) {
  transaction(db, () => {
    ownerRun(db, runId, owner);
    const source = db
      .prepare(sourceQuery + ' WHERE p.posting_id=?')
      .get(item.posting_id);
    if (!source || inputHash(source) !== item.input_hash)
      result = { status: 'stale' };
    db.prepare(
      "UPDATE country_inference_items SET status=?,country=?,confidence=?,reason=?,evidence_json=?,origin_run_id=? WHERE run_id=? AND posting_id=? AND status='running'",
    ).run(
      result.status,
      result.country ?? null,
      result.confidence ?? null,
      result.reason ?? null,
      JSON.stringify(result.evidence ?? []),
      runId,
      runId,
      item.posting_id,
    );
  });
}
export const countryCounts = (db, runId) =>
  db
    .prepare(
      'SELECT status,count(*) count FROM country_inference_items WHERE run_id=? GROUP BY status',
    )
    .all(runId);
