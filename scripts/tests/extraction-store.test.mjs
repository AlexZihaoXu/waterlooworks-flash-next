import assert from 'node:assert/strict';
import test from 'node:test';
import { DatabaseSync } from 'node:sqlite';
import {
  transaction,
  codePointOffset,
  assertOwner,
  counts,
} from '../extraction-store.mjs';

test('rolls back interrupted transactions on a real empty SQLite database', () => {
  const db = new DatabaseSync(':memory:');
  try {
    db.exec('CREATE TABLE primitives(value INTEGER) STRICT');
    assert.throws(() =>
      transaction(db, () => {
        db.exec('INSERT INTO primitives VALUES(1)');
        throw new Error('stop');
      }),
    );
    assert.equal(db.prepare('SELECT count(*) n FROM primitives').get().n, 0);
    transaction(db, () => db.exec('INSERT INTO primitives VALUES(1)'));
    assert.equal(db.prepare('SELECT count(*) n FROM primitives').get().n, 1);
  } finally {
    db.close();
  }
});
test('missing ownership never authorizes persistence', () => {
  const db = new DatabaseSync(':memory:');
  try {
    db.exec('CREATE TABLE source_extraction_runs(run_id TEXT) STRICT');
    assert.throws(() => assertOwner(db, 'missing', 'missing'), /lease lost/);
  } finally {
    db.close();
  }
});
test('scopes progress counts to the selected run', () => {
  const db = new DatabaseSync(':memory:');
  try {
    db.exec(
      "CREATE TABLE source_extraction_items(run_id TEXT,status TEXT) STRICT; INSERT INTO source_extraction_items VALUES('a','pending'),('b','accepted')",
    );
    const result = counts(db, 'a');
    assert.equal(result.length, 1);
    assert.equal(result[0].status, 'pending');
    assert.equal(result[0].count, 1);
  } finally {
    db.close();
  }
});

test('converts JavaScript UTF-16 offsets to database Unicode code points', () => {
  assert.equal(codePointOffset('😀C++', 2), 1);
  assert.equal(codePointOffset('😀C++', 5), 4);
});
