import { randomUUID } from 'node:crypto';
import { extractionParts } from './extraction-engine.mjs';
import { normalizeTerm } from './extraction-lib.mjs';
import { compatibleOrigin } from './inference-reuse.mjs';

const now = () => new Date().toISOString();
export function transaction(db, fn) {
  db.exec('BEGIN IMMEDIATE');
  try {
    const result = fn();
    db.exec('COMMIT');
    return result;
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
}
export function assertIdleSource(db) {
  if (
    db
      .prepare("SELECT 1 FROM collection_runs WHERE status='running' LIMIT 1")
      .get() ||
    db
      .prepare(
        "SELECT 1 FROM inference_jobs WHERE status IN ('running','stopping') LIMIT 1",
      )
      .get()
  )
    throw new Error('Another collection or legacy inference job is active');
}
export function assertOwner(db, runId, owner) {
  const run = db
    .prepare('SELECT * FROM source_extraction_runs WHERE run_id=?')
    .get(runId);
  if (
    !run ||
    run.owner !== owner ||
    run.status !== 'running' ||
    run.lease_until_ms < Date.now()
  )
    throw new Error('Extraction lease lost');
  return run;
}
export function createRun(db, engine, ids, limit) {
  return transaction(db, () => {
    assertIdleSource(db);
    if (
      db
        .prepare(
          "SELECT 1 FROM source_extraction_runs WHERE status='running' AND lease_until_ms>=?",
        )
        .get(Date.now())
    )
      throw new Error('An extraction already owns the database');
    db.prepare(
      "UPDATE source_extraction_runs SET status='stopped',owner=NULL WHERE status='running' AND lease_until_ms<?",
    ).run(Date.now());
    // Snapshot latest intent BEFORE inserting the replacement run. Carry every
    // compatible current item, even outside a bounded selection, atomically.
    const priorRuns = Object.fromEntries(
      ['salary', 'keywords'].map((mode) => [
        mode,
        db
          .prepare(
            'SELECT r.* FROM source_extraction_runs r WHERE EXISTS (SELECT 1 FROM source_extraction_items i WHERE i.run_id=r.run_id AND i.mode=?) ORDER BY created_at DESC,run_id DESC LIMIT 1',
          )
          .get(mode),
      ]),
    );
    const runId = randomUUID(),
      owner = randomUUID();
    db.prepare(
      "INSERT INTO source_extraction_runs(run_id,configuration_hash,configuration_json,status,owner,lease_until_ms,created_at,updated_at) VALUES(?,?,?,'running',?,?,?,?)",
    ).run(
      runId,
      engine.configurationHash,
      JSON.stringify(engine.configuration),
      owner,
      Date.now() + 120000,
      now(),
      now(),
    );
    const allSources = db
      .prepare(
        'SELECT c.posting_id,c.content_hash,c.content_json,(SELECT max(r.revision_id) FROM posting_content_revisions r WHERE r.posting_id=c.posting_id AND r.content_hash=c.content_hash) revision_id FROM posting_contents c ORDER BY CAST(c.posting_id AS INTEGER)',
      )
      .all();
    const sources = allSources
      .filter((r) => !ids.length || ids.includes(r.posting_id))
      .slice(0, limit);
    if (ids.some((id) => !sources.some((s) => s.posting_id === id)))
      throw new Error('A selected posting has no saved content');
    const insert = db.prepare(
      'INSERT INTO source_extraction_items(run_id,posting_id,content_revision_id,content_hash,mode,part,status,extraction_id,rejected_count) VALUES(?,?,?,?,?,?,?,?,?)',
    );
    const selectedIds = new Set(sources.map((source) => source.posting_id));
    for (const source of allSources)
      for (const task of extractionParts(
        JSON.parse(source.content_json).fields,
      )) {
        const prior = priorRuns[task.mode];
        const selected = selectedIds.has(source.posting_id);
        if (
          !selected &&
          prior &&
          db
            .prepare(
              "SELECT 1 FROM source_extraction_items WHERE run_id=? AND posting_id=? AND mode=? AND status IN ('pending','running') LIMIT 1",
            )
            .get(prior.run_id, source.posting_id, task.mode)
        )
          continue;
        let cached =
          prior &&
          db
            .prepare(
              `SELECT e.extraction_id,e.rejected_count,
          origin.configuration_json,origin.configuration_hash
          FROM source_extraction_items i JOIN source_extractions e USING(extraction_id)
          JOIN source_extraction_runs origin ON origin.run_id=e.run_id
          WHERE i.run_id=? AND i.posting_id=? AND i.content_hash=? AND i.content_revision_id=?
          AND i.mode=? AND i.part=? AND i.status IN ('accepted','partial')
          AND e.posting_id=i.posting_id AND e.content_hash=i.content_hash
          AND e.content_revision_id=i.content_revision_id AND e.mode=i.mode AND e.part=i.part
          AND e.configuration_hash=origin.configuration_hash AND e.rejected_count=i.rejected_count`,
            )
            .get(
              prior.run_id,
              source.posting_id,
              source.content_hash,
              source.revision_id,
              task.mode,
              task.part,
            );
        if (
          cached &&
          (!compatibleOrigin(cached, engine, 'extraction') ||
            !compatibleOrigin(
              cached,
              {
                configuration: JSON.parse(prior.configuration_json),
                configurationHash: prior.configuration_hash,
              },
              'extraction',
            ))
        )
          cached = null;
        const priorFailure =
          !selected &&
          prior &&
          compatibleOrigin(prior, engine, 'extraction') &&
          db
            .prepare(
              "SELECT status,rejected_count FROM source_extraction_items WHERE run_id=? AND posting_id=? AND content_hash=? AND content_revision_id=? AND mode=? AND part=? AND status IN ('failed','rejected','stale')",
            )
            .get(
              prior.run_id,
              source.posting_id,
              source.content_hash,
              source.revision_id,
              task.mode,
              task.part,
            );
        if (!selected && !cached && !priorFailure) continue;
        insert.run(
          runId,
          source.posting_id,
          source.revision_id,
          source.content_hash,
          task.mode,
          task.part,
          cached
            ? cached.rejected_count
              ? 'partial'
              : 'accepted'
            : (priorFailure?.status ?? 'pending'),
          cached?.extraction_id ?? null,
          cached?.rejected_count ?? priorFailure?.rejected_count ?? 0,
        );
      }
    return { runId, owner, postings: sources.length };
  });
}
export function resumeRun(db, engine, runId) {
  return transaction(db, () => {
    assertIdleSource(db);
    const old = db
      .prepare('SELECT * FROM source_extraction_runs WHERE run_id=?')
      .get(runId);
    if (!old || old.configuration_hash !== engine.configurationHash)
      throw new Error('Resume requires the original model, prompts and code');
    if (old.status === 'completed') throw new Error('Run already completed');
    if (
      db
        .prepare(
          "SELECT 1 FROM source_extraction_runs WHERE status='running' AND lease_until_ms>=?",
        )
        .get(Date.now())
    )
      throw new Error('An extraction still holds a live lease');
    db.prepare(
      "UPDATE source_extraction_runs SET status='stopped',owner=NULL WHERE status='running' AND lease_until_ms<?",
    ).run(Date.now());
    const owner = randomUUID();
    db.prepare(
      "UPDATE source_extraction_runs SET status='running',owner=?,lease_until_ms=?,stop_requested=0,updated_at=? WHERE run_id=?",
    ).run(owner, Date.now() + 120000, now(), runId);
    db.prepare(
      engine.configuration.provider === 'openai-responses'
        ? "UPDATE source_extraction_items SET status=CASE WHEN attempts=0 THEN 'pending' ELSE 'failed' END WHERE run_id=? AND status IN ('running','failed')"
        : "UPDATE source_extraction_items SET status=CASE WHEN attempts<2 THEN 'pending' ELSE 'failed' END WHERE run_id=? AND status IN ('running','failed')",
    ).run(runId);
    return { runId, owner };
  });
}
// Reservations belong to this run's process lease, not to provider KV slots.
// A worker retains its posting between slices, including the checkpoint/claim gap.
export class PostingQueue {
  #postings = new Map();
  select(workerId, pendingIds) {
    const held = this.#postings.get(workerId);
    if (held && pendingIds.includes(held)) return held;
    this.#postings.delete(workerId);
    const reserved = new Set(this.#postings.values());
    const next = pendingIds.find((id) => !reserved.has(id));
    if (next) this.#postings.set(workerId, next);
    return next ?? null;
  }
}
export function pendingItem(db, runId, postingQueue, workerId) {
  if (!postingQueue)
    return (
      db
        .prepare(
          "SELECT * FROM source_extraction_items WHERE run_id=? AND status='pending' ORDER BY CAST(posting_id AS INTEGER),mode,part LIMIT 1",
        )
        .get(runId) ?? null
    );
  const available = db
    .prepare(
      `SELECT DISTINCT i.posting_id FROM source_extraction_items i
    WHERE i.run_id=? AND i.status='pending' AND NOT EXISTS (
      SELECT 1 FROM source_extraction_items active WHERE active.run_id=i.run_id
      AND active.posting_id=i.posting_id AND active.status='running')
    ORDER BY CAST(i.posting_id AS INTEGER)`,
    )
    .all(runId)
    .map((row) => row.posting_id);
  const postingId = postingQueue.select(workerId, available);
  if (postingId === null) return null;
  return (
    db
      .prepare(
        "SELECT * FROM source_extraction_items WHERE run_id=? AND posting_id=? AND status='pending' ORDER BY mode,part LIMIT 1",
      )
      .get(runId, postingId) ?? null
  );
}
export function claimItem(db, runId, owner, postingQueue, workerId) {
  return transaction(db, () => {
    const run = assertOwner(db, runId, owner);
    if (run.stop_requested) return null;
    assertIdleSource(db);
    const item = pendingItem(db, runId, postingQueue, workerId);
    if (!item) return null;
    const source = db
      .prepare(
        'SELECT content_hash,content_json FROM posting_contents WHERE posting_id=?',
      )
      .get(item.posting_id);
    if (!source || source.content_hash !== item.content_hash) {
      db.prepare(
        "UPDATE source_extraction_items SET status='stale' WHERE run_id=? AND posting_id=? AND mode=? AND part=?",
      ).run(runId, item.posting_id, item.mode, item.part);
      return { stale: true };
    }
    db.prepare(
      "UPDATE source_extraction_items SET status='running',attempts=attempts+1 WHERE run_id=? AND posting_id=? AND mode=? AND part=?",
    ).run(runId, item.posting_id, item.mode, item.part);
    return { ...item, fields: JSON.parse(source.content_json).fields };
  });
}
export const codePointOffset = (source, index) =>
  Array.from(source.slice(0, index)).length;
export function persistItem(db, engine, runId, owner, item, result) {
  transaction(db, () => {
    assertOwner(db, runId, owner);
    const current = db
      .prepare('SELECT content_hash FROM posting_contents WHERE posting_id=?')
      .get(item.posting_id);
    let status =
      current?.content_hash === item.content_hash ? result.status : 'stale';
    let extractionId = null;
    if (['accepted', 'partial'].includes(status)) {
      const inserted = db
        .prepare(
          'INSERT INTO source_extractions(run_id,posting_id,content_revision_id,content_hash,configuration_hash,mode,part,rejected_count,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
        )
        .run(
          runId,
          item.posting_id,
          item.content_revision_id,
          item.content_hash,
          engine.configurationHash,
          item.mode,
          item.part,
          result.rejected.length,
          now(),
        );
      extractionId = Number(inserted.lastInsertRowid);
      const term = db.prepare(
        'INSERT OR IGNORE INTO source_extraction_terms(extraction_id,name,field,start,end) VALUES(?,?,?,?,?)',
      );
      const pay = db.prepare(
        'INSERT INTO source_extraction_pay(extraction_id,ordinal,minimum,maximum,currency,period,bound_type,minimum_inclusive,maximum_inclusive,field,quote_start,quote_end,amount_start,amount_end) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
      );
      let ordinal = 0;
      for (const entry of result.accepted) {
        const source = item.fields[entry.field];
        if (typeof source !== 'string')
          throw new Error('Source field unavailable at persistence');
        if (item.mode === 'keywords')
          for (const span of entry.spans) {
            if (source.slice(span.start, span.end) !== entry.text)
              throw new Error('Term evidence changed');
            term.run(
              extractionId,
              normalizeTerm(entry.text),
              entry.field,
              codePointOffset(source, span.start),
              codePointOffset(source, span.end),
            );
          }
        else
          for (const rate of entry.rates) {
            const quote = entry.spans[0];
            if (source.slice(quote.start, quote.end) !== entry.text)
              throw new Error('Pay evidence changed');
            pay.run(
              extractionId,
              ordinal++,
              rate.minimum,
              rate.maximum,
              rate.currency,
              rate.period,
              rate.bound_type,
              rate.minimum_inclusive === null
                ? null
                : Number(rate.minimum_inclusive),
              rate.maximum_inclusive === null
                ? null
                : Number(rate.maximum_inclusive),
              entry.field,
              codePointOffset(source, quote.start),
              codePointOffset(source, quote.end),
              codePointOffset(source, quote.start + rate.source_start),
              codePointOffset(source, quote.start + rate.source_end),
            );
          }
      }
    }
    const tokens = (v) => (Number.isSafeInteger(v) && v >= 0 ? v : null);
    db.prepare(
      "UPDATE source_extraction_items SET status=?,error_category=?,rejected_count=?,prompt_tokens=?,completion_tokens=?,reasoning_tokens=?,seconds=?,extraction_id=? WHERE run_id=? AND posting_id=? AND mode=? AND part=? AND status='running'",
    ).run(
      status,
      result.rejected.length ? 'validation' : null,
      result.rejected.length,
      tokens(result.usage.prompt_tokens),
      tokens(result.usage.completion_tokens),
      tokens(result.usage.reasoning_tokens),
      result.seconds,
      extractionId,
      runId,
      item.posting_id,
      item.mode,
      item.part,
    );
  });
}
export function counts(db, runId) {
  return db
    .prepare(
      'SELECT status,count(*) AS count FROM source_extraction_items WHERE run_id=? GROUP BY status ORDER BY status',
    )
    .all(runId);
}
