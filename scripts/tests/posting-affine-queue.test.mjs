import assert from 'node:assert/strict';
import test from 'node:test';
import { DatabaseSync } from 'node:sqlite';
import {
  PostingQueue,
  pendingItem,
  counts,
  resumeRun,
  claimItem,
} from '../extraction-store.mjs';

function queueDatabase() {
  const db = new DatabaseSync(':memory:');
  db.exec(
    `CREATE TABLE source_extraction_items(run_id TEXT,posting_id TEXT,mode TEXT,part TEXT,status TEXT) STRICT`,
  );
  return db;
}
function add(db, run, id, part, status = 'pending') {
  db.prepare(
    "INSERT INTO source_extraction_items(run_id,posting_id,mode,part,status) VALUES(?,?,'keywords',?,?)",
  ).run(run, id, part, status);
}
function mark(db, item, status) {
  db.prepare(
    'UPDATE source_extraction_items SET status=? WHERE run_id=? AND posting_id=? AND part=?',
  ).run(status, item.run_id, item.posting_id, item.part);
}
test('posting reservations drain one posting sequentially while other workers take distinct IDs', () => {
  const db = queueDatabase();
  try {
    for (const id of ['1', '2', '3'])
      for (const part of ['all', 'f0']) add(db, 'r', id, part);
    const queue = new PostingQueue();
    const a = pendingItem(db, 'r', queue, 0);
    mark(db, a, 'running');
    const b = pendingItem(db, 'r', queue, 1);
    mark(db, b, 'running');
    assert.equal(a.posting_id, '1');
    assert.equal(b.posting_id, '2');
    mark(db, b, 'accepted');
    const nextB = pendingItem(db, 'r', queue, 1);
    assert.equal(nextB.posting_id, '2');
    assert.equal(nextB.part, 'f0');
    mark(db, nextB, 'accepted');
    assert.equal(pendingItem(db, 'r', queue, 1).posting_id, '3');
    mark(db, a, 'accepted');
    assert.equal(pendingItem(db, 'r', queue, 0).posting_id, '1');
  } finally {
    db.close();
  }
});
test('temporarily blocked pending work is drained by its owner, not mistaken for completed work', () => {
  const db = queueDatabase();
  try {
    add(db, 'r', '1', 'all');
    add(db, 'r', '1', 'f0');
    const queue = new PostingQueue();
    const first = pendingItem(db, 'r', queue, 0);
    mark(db, first, 'running');
    assert.equal(pendingItem(db, 'r', queue, 1), null);
    assert.equal(counts(db, 'r').find((x) => x.status === 'pending').count, 1);
    mark(db, first, 'accepted');
    const next = pendingItem(db, 'r', queue, 0);
    assert.equal(next.part, 'f0');
    mark(db, next, 'accepted');
    assert.equal(pendingItem(db, 'r', queue, 0), null);
    assert.deepEqual(
      counts(db, 'r').map((x) => [x.status, x.count]),
      [['accepted', 2]],
    );
  } finally {
    db.close();
  }
});
test('default selector keeps v7 slice scheduling; opt-in is run-scoped and skips terminal work', () => {
  const db = queueDatabase();
  try {
    add(db, 'other', '0', 'all');
    add(db, 'r', '1', 'all', 'running');
    add(db, 'r', '1', 'f0');
    add(db, 'r', '2', 'all', 'stale');
    add(db, 'r', '3', 'all');
    assert.equal(pendingItem(db, 'r').posting_id, '1');
    assert.equal(pendingItem(db, 'r', new PostingQueue(), 0).posting_id, '3');
  } finally {
    db.close();
  }
});
test('four interleaved primitive workers never overlap a posting and exhaust all slices', async () => {
  const db = queueDatabase(),
    queue = new PostingQueue(),
    owners = new Map(),
    active = new Set();
  try {
    for (let id = 1; id <= 8; id++)
      for (const part of ['all', 'f0', 'f1']) add(db, 'r', String(id), part);
    await Promise.all(
      Array.from({ length: 4 }, async (_, workerId) => {
        for (;;) {
          const item = pendingItem(db, 'r', queue, workerId);
          if (!item) return;
          assert.ok(!active.has(item.posting_id));
          if (owners.has(item.posting_id))
            assert.equal(owners.get(item.posting_id), workerId);
          owners.set(item.posting_id, workerId);
          active.add(item.posting_id);
          mark(db, item, 'running');
          await Promise.resolve();
          active.delete(item.posting_id);
          mark(db, item, 'accepted');
        }
      }),
    );
    assert.equal(owners.size, 8);
    assert.deepEqual(
      counts(db, 'r').map((x) => [x.status, x.count]),
      [['accepted', 24]],
    );
  } finally {
    db.close();
  }
});
test('stop and expired/mismatched run leases guard shared claims before source access', () => {
  const db = new DatabaseSync(':memory:');
  try {
    db.exec(
      `CREATE TABLE source_extraction_runs(run_id TEXT,status TEXT,owner TEXT,lease_until_ms INTEGER,stop_requested INTEGER) STRICT`,
    );
    db.prepare(
      "INSERT INTO source_extraction_runs VALUES('r','running','o',?,1)",
    ).run(Date.now() + 60000);
    assert.equal(claimItem(db, 'r', 'o', new PostingQueue(), 0), null);
    assert.throws(
      () => claimItem(db, 'r', 'wrong', new PostingQueue(), 0),
      /lease lost/,
    );
    db.exec(
      'UPDATE source_extraction_runs SET lease_until_ms=0,stop_requested=0',
    );
    assert.throws(
      () => claimItem(db, 'r', 'o', new PostingQueue(), 0),
      /lease lost/,
    );
    assert.equal(db.isTransaction, false);
  } finally {
    db.close();
  }
});
test('explicit expired-run resume retains terminal checkpoints and the existing attempt bound', () => {
  const db = queueDatabase();
  try {
    db.exec(`CREATE TABLE collection_runs(status TEXT) STRICT;
      CREATE TABLE inference_jobs(status TEXT) STRICT;
      CREATE TABLE source_extraction_runs(run_id TEXT,configuration_hash TEXT,status TEXT,owner TEXT,lease_until_ms INTEGER,stop_requested INTEGER,updated_at TEXT) STRICT;
      INSERT INTO source_extraction_runs VALUES('r','same','running','old',0,1,'');
      ALTER TABLE source_extraction_items ADD COLUMN attempts INTEGER DEFAULT 1`);
    add(db, 'r', '1', 'all', 'running');
    add(db, 'r', '1', 'f0', 'accepted');
    add(db, 'r', '2', 'all', 'running');
    db.exec(
      "UPDATE source_extraction_items SET attempts=2 WHERE posting_id='2'",
    );
    const ownership = resumeRun(
      db,
      {
        configurationHash: 'same',
        configuration: { provider: 'local-benchmark' },
      },
      'r',
    );
    assert.notEqual(ownership.owner, 'old');
    assert.deepEqual(
      counts(db, 'r').map((x) => [x.status, x.count]),
      [
        ['accepted', 1],
        ['failed', 1],
        ['pending', 1],
      ],
    );
    assert.equal(
      db.prepare('SELECT stop_requested FROM source_extraction_runs').get()
        .stop_requested,
      0,
    );
    assert.equal(pendingItem(db, 'r', new PostingQueue(), 0).posting_id, '1');
  } finally {
    db.close();
  }
});
test('empty queues execute both SQL paths without source data', () => {
  const db = queueDatabase();
  try {
    assert.equal(pendingItem(db, 'r'), null);
    assert.equal(pendingItem(db, 'r', new PostingQueue(), 0), null);
  } finally {
    db.close();
  }
});
test('resume refuses a different profile/configuration before changing checkpoints', () => {
  const db = new DatabaseSync(':memory:');
  try {
    db.exec(`CREATE TABLE collection_runs(status TEXT) STRICT;
      CREATE TABLE inference_jobs(status TEXT) STRICT;
      CREATE TABLE source_extraction_runs(run_id TEXT,configuration_hash TEXT,status TEXT) STRICT;
      INSERT INTO source_extraction_runs VALUES('r','old','stopped')`);
    assert.throws(
      () => resumeRun(db, { configurationHash: 'new' }, 'r'),
      /original model, prompts and code/,
    );
    assert.equal(
      db.prepare('SELECT status FROM source_extraction_runs').get().status,
      'stopped',
    );
    assert.equal(db.isTransaction, false);
  } finally {
    db.close();
  }
});
