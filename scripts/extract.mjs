import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';
import { safeDatasetPath } from './benchmark-lib.mjs';
import { counts } from './extraction-store.mjs';

async function main() {
  const args = process.argv.slice(2),
    action = args.shift() ?? 'help';
  if (['help', '--help'].includes(action)) {
    console.log(`Usage: npm run extract -- start --all [--concurrency 4] [--limit N]
       npm run extract -- start --posting-id ID
       npm run extract -- status [RUN_ID]
       npm run extract -- stop RUN_ID
       npm run extract -- resume RUN_ID
Options: --database PATH (default data/waterlooworks.sqlite3), --hours 10.
Opt-in: --shared-job-prefix uses v8-shared-job-prefix and sequential slices per posting.
Omit it for the validated v7 prompt/scheduling. Resume must repeat the original flag and bounds.
Shared prefix is experimental: no guaranteed provider slot affinity or KV retention.
status/stop only: start/resume used the removed OpenAI Responses bridge;
AI extraction now runs through the Pi sidecar during refresh (docs/pi-inference.md).
No refresh, model loading, source updates or replacement of existing projections.
Checkpointed source index/pay candidates are separate from authoritative UI analyses.`);
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
    all = false,
    sharedJobPrefix = false;
  const ids = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--all') {
      all = true;
      continue;
    }
    if (args[i] === '--shared-job-prefix') {
      sharedJobPrefix = true;
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
    ![9, 10, 11, 12, 13, 14, 15, 16].includes(
      db.prepare('PRAGMA user_version').get().user_version,
    )
  ) {
    db.close();
    throw new Error(
      'Schema9 required; migrate a backed-up database with the backend first',
    );
  }
  if (action === 'status') {
    const rows = runId
      ? db
          .prepare(
            'SELECT run_id,status,created_at,updated_at,stop_requested,lease_until_ms FROM source_extraction_runs WHERE run_id=?',
          )
          .all(runId)
      : db
          .prepare(
            'SELECT run_id,status,created_at,updated_at,stop_requested,lease_until_ms FROM source_extraction_runs ORDER BY created_at DESC LIMIT 5',
          )
          .all();
    for (const row of rows) {
      console.log({
        ...row,
        leaseExpired:
          row.status === 'running' && row.lease_until_ms < Date.now(),
      });
      console.table(counts(db, row.run_id));
    }
    db.close();
    return;
  }
  if (action === 'stop') {
    const changed = db
      .prepare(
        "UPDATE source_extraction_runs SET stop_requested=1 WHERE run_id=? AND status='running'",
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
  db.close();
  // The only paid provider was the removed Python OpenAI Responses bridge.
  // AI extraction now runs through the Pi sidecar (docs/pi-inference.md).
  throw new Error('Pi extraction replaces this CLI');
}
main().catch((error) => {
  console.error(
    error?.message === 'Invalid work bounds'
      ? 'Invalid work bounds'
      : error?.message === 'Pi extraction replaces this CLI'
        ? 'Source extraction start/resume was removed with the OpenAI Responses bridge; AI extraction runs through the Pi sidecar during refresh.'
        : 'Extraction status did not proceed; check schema and bounds. Diagnostics suppressed.',
  );
  process.exitCode = 1;
});
