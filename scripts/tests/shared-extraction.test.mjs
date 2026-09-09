import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import {
  extractionMessages,
  extractionProfile,
  withinContextBudget,
} from '../extraction-engine.mjs';
import { sourceFields } from '../extraction-lib.mjs';
import {
  sharedCountryMessages,
  sharedJobPrefix,
} from '../shared-job-prompt.mjs';

const fields = { 'Required Skills:': 'β', 'Job Summary:': 'α' };
const prompts = {
  salary: 'salary instruction',
  keywords: 'keyword instruction',
};

test('v7 remains the default profile with the exact legacy message layout', () => {
  assert.deepEqual(extractionProfile(false), { profile: 'v7' });
  const selected = sourceFields(fields, 'keywords');
  assert.deepEqual(
    extractionMessages(fields, selected, prompts.keywords, false),
    [
      { role: 'system', content: prompts.keywords },
      { role: 'user', content: JSON.stringify(selected) },
    ],
  );
});
test('opt-in profile has explicit prefix/scheduling identity and no duplicated source suffix', () => {
  assert.equal(extractionProfile(true).profile, 'v8-shared-job-prefix');
  assert.equal(extractionProfile(true).prefixVersion, 'shared-job-details-v1');
  assert.equal(extractionProfile(true).scheduling, 'posting-affine-v1');
  const all = sourceFields(fields, 'keywords');
  const selected = { f1: all.f1 };
  const messages = extractionMessages(fields, selected, prompts.keywords, true);
  const prefix = sharedJobPrefix(fields);
  const task = JSON.parse(messages[1].content.slice(prefix.length));
  assert.deepEqual(task.source_fields, { f1: all.f1.name });
  assert.equal(messages[1].content.split('α').length - 1, 1);
  assert.equal(messages[1].content.split('β').length - 1, 1);
  assert.ok(!JSON.stringify(task).includes('α'));
  assert.ok(!JSON.stringify(task).includes('β'));
  assert.deepEqual(Object.keys(all), ['f0', 'f1']); // Canonical prefix sorting never renumbers source aliases.
});
test('salary, keyword slices and country composition share the identical complete prefix', () => {
  const keyword = extractionMessages(
    fields,
    sourceFields(fields, 'keywords'),
    prompts.keywords,
    true,
  );
  const salary = extractionMessages(
    fields,
    sourceFields(fields, 'salary'),
    prompts.salary,
    true,
  );
  const country = sharedCountryMessages(
    fields,
    { 'content:Job Summary:': 'α', 'listing:city': 'γ' },
    'country instruction',
  );
  const prefix = sharedJobPrefix(fields);
  for (const messages of [keyword, salary, country]) {
    assert.deepEqual(messages[0], keyword[0]);
    assert.equal(messages[1].content.slice(0, prefix.length), prefix);
  }
  const task = JSON.parse(country[1].content.slice(prefix.length));
  assert.deepEqual(task.source_fields, {
    'content:Job Summary:': 'Job Summary:',
  });
  assert.deepEqual(task.additional_fields, { 'listing:city': 'γ' });
  assert.ok(!prefix.includes('γ'));
  assert.throws(
    () =>
      sharedCountryMessages(fields, { 'content:Job Summary:': 'changed' }, 'x'),
    /source mapping/,
  );
});
test('whole request UTF-8 budget includes source, task, schema and output reserve', () => {
  const body = {
    messages: [{ content: '😀' }],
    response_format: { schema: 'x' },
  };
  const size = Buffer.byteLength(JSON.stringify(body));
  assert.equal(withinContextBudget(body, 1024, size + 1024 + 512), true);
  assert.equal(withinContextBudget(body, 1024, size + 1024 + 511), false);
  assert.equal(withinContextBudget(body, 1024, NaN), false);
});
test('opt-in concurrency bounds fail before database access or model discovery', () => {
  for (const value of ['0', '5', '1.5']) {
    const result = spawnSync(
      process.execPath,
      [
        'scripts/extract.mjs',
        'start',
        '--all',
        '--shared-job-prefix',
        '--concurrency',
        value,
        '--database',
        'does-not-exist.sqlite3',
      ],
      { encoding: 'utf8' },
    );
    assert.equal(result.status, 1);
    assert.ok(result.stderr.includes('Invalid work bounds'));
  }
});
test('shared builder is hashed and CLI opt-in is discoverable without model discovery', () => {
  const source = readFileSync(
    new URL('../extraction-engine.mjs', import.meta.url),
    'utf8',
  );
  assert.ok(source.includes("'shared-job-prompt.mjs'"));
  const result = spawnSync(
    process.execPath,
    ['scripts/extract.mjs', '--help'],
    { encoding: 'utf8' },
  );
  assert.equal(result.status, 0);
  assert.ok(result.stdout.includes('--shared-job-prefix'));
});
