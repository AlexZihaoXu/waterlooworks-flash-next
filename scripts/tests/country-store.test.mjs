import assert from 'node:assert/strict';
import test from 'node:test';
import { DatabaseSync } from 'node:sqlite';
import { spawnSync } from 'node:child_process';
import { ownerRun, countryCounts, inputHash } from '../country-store.mjs';

test('ownership requires the same run, owner, active status and unexpired lease', () => {
  const db = new DatabaseSync(':memory:');
  try {
    db.exec(
      'CREATE TABLE country_inference_runs(run_id TEXT, owner TEXT, status TEXT, lease_until_ms INTEGER) STRICT',
    );
    assert.throws(() => ownerRun(db, 'a', 'b'), /lease_lost/);
    db.prepare('INSERT INTO country_inference_runs VALUES(?,?,?,?)').run(
      'a',
      'b',
      'running',
      Date.now() + 60000,
    );
    assert.equal(ownerRun(db, 'a', 'b').run_id, 'a');
    assert.throws(() => ownerRun(db, 'a', 'c'), /lease_lost/);
    db.exec('UPDATE country_inference_runs SET lease_until_ms=0');
    assert.throws(() => ownerRun(db, 'a', 'b'), /lease_lost/);
    db.exec(
      "UPDATE country_inference_runs SET status='stopped', lease_until_ms=9999999999999",
    );
    assert.throws(() => ownerRun(db, 'a', 'b'), /lease_lost/);
  } finally {
    db.close();
  }
});
test('input identity includes both listing and optional content, including null transitions', () => {
  const value = inputHash({ listing_hash: 'a', content_hash: null });
  assert.notEqual(value, inputHash({ listing_hash: 'b', content_hash: null }));
  assert.notEqual(value, inputHash({ listing_hash: 'a', content_hash: 'c' }));
  assert.equal(value, inputHash({ listing_hash: 'a', content_hash: null }));
});
test('status counts are scoped to the requested run', () => {
  const db = new DatabaseSync(':memory:');
  try {
    db.exec(
      "CREATE TABLE country_inference_items(run_id TEXT,status TEXT) STRICT; INSERT INTO country_inference_items VALUES('a','unknown'),('b','accepted')",
    );
    const rows = countryCounts(db, 'a');
    assert.equal(rows.length, 1);
    assert.equal(rows[0].status, 'unknown');
  } finally {
    db.close();
  }
});
test('invalid CLI concurrency is rejected before database access or model discovery', () => {
  for (const value of ['0', '5', 'NaN']) {
    const result = spawnSync(
      process.execPath,
      ['scripts/country.mjs', 'start', '--all', '--concurrency', value],
      { encoding: 'utf8' },
    );
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Diagnostics suppressed/);
  }
});
