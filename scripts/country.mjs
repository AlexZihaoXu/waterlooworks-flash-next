import fs from 'node:fs/promises';
import { OpenAIBridge } from './openai-bridge.mjs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';
import { safeDatasetPath } from './benchmark-lib.mjs';
import { loadCountryEngine } from './country-engine.mjs';
import { inferCountry } from './country-engine.mjs';
import {
  beginCountryRun,
  claimCountry,
  persistCountry,
  countryCounts,
} from './country-store.mjs';

async function main() {
  const args = process.argv.slice(2),
    action = args.shift() ?? 'help';
  if (['help', '--help'].includes(action)) {
    console.log(`Usage: npm run country -- start --all [--concurrency 4] [--limit N]
       npm run country -- start --posting-id ID
       npm run country -- status [RUN_ID]
       npm run country -- stop RUN_ID
       npm run country -- resume RUN_ID
Options: --database PATH (default data/waterlooworks.sqlite3), --hours 10.
Requires schema10/11/12/13/14, schema14 for OpenAI gpt-5.6-luna/low; <=4 paid requests at once.
No refresh, model loading, source updates or replacement of existing projections.
Country predictions are separate from verified source facts. No salary/keyword calls.
Resume never reissues terminal results or lost in-flight calls; start a new explicit run to retry failures.`);
    return;
  }
  if (!['start', 'status', 'stop', 'resume'].includes(action))
    throw new Error('Unknown action');
  let runId =
    ['status', 'stop', 'resume'].includes(action) &&
    args[0] &&
    !args[0].startsWith('--')
      ? args.shift()
      : null;
  let database = 'data/waterlooworks.sqlite3',
    limit = 10000,
    concurrency = 4,
    hours = 10,
    all = false;
  const ids = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--all') {
      all = true;
      continue;
    }
    if (!args[i + 1]) throw new Error('Missing option value');
    const option = args[i],
      value = args[++i];
    if (option === '--database') database = value;
    else if (option === '--limit') limit = Number(value);
    else if (option === '--concurrency') concurrency = Number(value);
    else if (option === '--hours') hours = Number(value);
    else if (option === '--posting-id' && /^\d+$/.test(value)) ids.push(value);
    else throw new Error('Unknown or invalid option');
  }
  if (
    !Number.isInteger(limit) ||
    limit < 1 ||
    limit > 10000 ||
    !Number.isInteger(concurrency) ||
    concurrency < 1 ||
    concurrency > 4 ||
    !Number.isFinite(hours) ||
    hours <= 0 ||
    hours > 24
  )
    throw new Error('Invalid work bounds');
  if (action === 'start' && all === Boolean(ids.length))
    throw new Error('Select --all OR saved --posting-id IDs');
  if (['stop', 'resume'].includes(action) && !runId)
    throw new Error('Run ID required');
  if (runId && !/^[0-9a-f-]{36}$/.test(runId))
    throw new Error('Invalid run ID');
  process.chdir(fileURLToPath(new URL('../', import.meta.url)));
  const dbPath = safeDatasetPath(await fs.realpath(safeDatasetPath(database)));
  const db = new DatabaseSync(dbPath, { readOnly: action === 'status' });
  db.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000');
  if (
    ![10, 11, 12, 13, 14].includes(
      db.prepare('PRAGMA user_version').get().user_version,
    )
  ) {
    db.close();
    throw new Error(
      'Schema10 required; migrate a backed-up database with the backend first',
    );
  }
  if (action === 'status') {
    const rows = runId
      ? db
          .prepare(
            'SELECT run_id,status,created_at,updated_at,stop_requested,lease_until_ms FROM country_inference_runs WHERE run_id=?',
          )
          .all(runId)
      : db
          .prepare(
            'SELECT run_id,status,created_at,updated_at,stop_requested,lease_until_ms FROM country_inference_runs ORDER BY created_at DESC LIMIT 5',
          )
          .all();
    for (const row of rows) {
      console.log({
        ...row,
        leaseExpired:
          row.status === 'running' && row.lease_until_ms < Date.now(),
      });
      console.table(countryCounts(db, row.run_id));
    }
    db.close();
    return;
  }
  if (action === 'stop') {
    const changed = db
      .prepare(
        "UPDATE country_inference_runs SET stop_requested=1 WHERE run_id=? AND status='running'",
      )
      .run(runId).changes;
    console.log(
      changed
        ? 'Stop requested; in-flight calls may finish.'
        : 'No matching running job.',
    );
    db.close();
    return;
  }
  let heartbeat;
  let bridge;
  let activeOwner;
  try {
    if (db.prepare('PRAGMA user_version').get().user_version !== 14)
      throw new Error('Schema14 is required for paid extraction');
    bridge = new OpenAIBridge();
    await bridge.initialize(dbPath);
    const engine = await loadCountryEngine({ concurrency, bridge });
    const ownership = beginCountryRun(
      db,
      engine,
      action === 'start' ? { ids, limit } : { runId },
    );
    runId = ownership.runId;
    const owner = ownership.owner;
    activeOwner = owner;
    await bridge.bind(runId, owner, 'country');
    console.log(
      'Paid OpenAI run: persisted request limit and known-cost stop threshold apply, not a hard monetary cap. Review admin usage before any new run after unknown usage.',
    );
    console.log(
      `Run ${runId}; model ${engine.configuration.model.id}; concurrency ${concurrency}; deadline ${hours}h`,
    );
    console.table(countryCounts(db, runId));
    let stopping = false,
      fatal = false,
      finished = 0;
    const deadline = Date.now() + hours * 3600000;
    process.on('SIGINT', () => {
      stopping = true;
      console.log('Stopping after current requests.');
    });
    process.on('SIGTERM', () => {
      stopping = true;
    });
    heartbeat = setInterval(() => {
      try {
        const changed = db
          .prepare(
            "UPDATE country_inference_runs SET lease_until_ms=?,updated_at=? WHERE run_id=? AND owner=? AND status='running' AND lease_until_ms>=?",
          )
          .run(
            Date.now() + 120000,
            new Date().toISOString(),
            runId,
            owner,
            Date.now(),
          ).changes;
        if (!changed) {
          stopping = true;
          fatal = true;
        }
      } catch {
        stopping = true;
        fatal = true;
      }
    }, 5000);
    async function worker() {
      while (!stopping && Date.now() < deadline) {
        let item;
        try {
          item = claimCountry(db, runId, owner);
          if (!item) return;
          if (item.stale) continue;
          const result = await inferCountry(
            engine,
            item.input,
            createHash('sha256')
              .update(
                JSON.stringify([
                  item.posting_id,
                  item.input_hash,
                  engine.configurationHash,
                ]),
              )
              .digest('hex'),
          );
          persistCountry(db, runId, owner, item, result);
          finished++;
          console.log(`${finished} checkpointed: ${result.status}`);
          if (finished % 25 === 0) console.table(countryCounts(db, runId));
        } catch {
          stopping = true;
          fatal = true;
          try {
            if (item?.posting_id)
              db.prepare(
                "UPDATE country_inference_items SET status='failed',reason='infrastructure' WHERE run_id=? AND posting_id=? AND status='running' AND EXISTS(SELECT 1 FROM country_inference_runs WHERE run_id=? AND owner=?)",
              ).run(runId, item.posting_id, runId, owner);
          } catch {
            /* Expired-lease recovery retains an interrupted item when the database is unavailable. */
          }
          console.error(
            'Country inference halted on model, transport, source-lock or persistence failure; committed evidence is retained.',
          );
        }
      }
    }
    const workers = await Promise.allSettled(
      Array.from({ length: concurrency }, () => worker()),
    );
    if (workers.some((r) => r.status === 'rejected')) fatal = true;
    clearInterval(heartbeat);
    const pending = db
      .prepare(
        "SELECT count(*) n FROM country_inference_items WHERE run_id=? AND status IN ('pending','running')",
      )
      .get(runId).n;
    const failures = db
      .prepare(
        "SELECT count(*) n FROM country_inference_items WHERE run_id=? AND status='failed'",
      )
      .get(runId).n;
    const status =
      fatal || failures ? 'failed' : pending ? 'stopped' : 'completed';
    db.prepare(
      'UPDATE country_inference_runs SET status=?,owner=NULL,lease_until_ms=0,updated_at=? WHERE run_id=? AND owner=?',
    ).run(status, new Date().toISOString(), runId, owner);
    console.log(`Run ${runId}: ${status}`);
    console.table(countryCounts(db, runId));
    if (fatal || failures) process.exitCode = 1;
    else if (pending) process.exitCode = 2;
  } finally {
    clearInterval(heartbeat);
    if (bridge) await bridge.close();
    if (activeOwner) {
      db.prepare(
        "UPDATE country_inference_runs SET status='stopped',owner=NULL,lease_until_ms=0 WHERE run_id=? AND owner=? AND status='running'",
      ).run(runId, activeOwner);
    }
    db.close();
  }
}
main().catch(() => {
  console.error(
    'Country inference did not proceed; check schema10/11/12/13/14, CLI bounds, idle sources OpenAI settings, usage and persisted budget limits. Diagnostics suppressed.',
  );
  process.exitCode = 1;
});
