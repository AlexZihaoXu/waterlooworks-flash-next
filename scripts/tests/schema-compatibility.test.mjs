import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { DatabaseSync } from 'node:sqlite';
import test from 'node:test';

for (const script of ['country', 'extract']) {
  for (const version of [12, 13, 14]) {
    test(`${script} status accepts additive admin-settings schema${version}`, () => {
      const directory = mkdtempSync(path.join(tmpdir(), 'ww-schema-'));
      try {
        const filename = path.join(directory, 'empty.sqlite3');
        const db = new DatabaseSync(filename);
        const table =
          script === 'country'
            ? 'country_inference_runs'
            : 'source_extraction_runs';
        db.exec(`PRAGMA user_version=${version}; CREATE TABLE ${table} (
        run_id TEXT, status TEXT, created_at TEXT, updated_at TEXT,
        stop_requested INTEGER, lease_until_ms INTEGER
      );`);
        db.close();
        const result = spawnSync(
          process.execPath,
          [`scripts/${script}.mjs`, 'status', '--database', filename],
          { encoding: 'utf8' },
        );
        assert.equal(result.status, 0);
        assert.equal(result.stdout, '');
      } finally {
        rmSync(directory, { recursive: true, force: true });
      }
    });
  }
}
