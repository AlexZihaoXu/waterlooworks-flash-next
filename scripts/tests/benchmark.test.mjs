import assert from 'node:assert/strict';
import test from 'node:test';
import {
  decimalKey,
  salaryKey,
  chooseReasoning,
  lexicalCoverage,
  quoteCheck,
  parseFinal,
  optionsFromArgs,
  benchmarkTable,
} from '../benchmark-lib.mjs';

test('builds labelled terminal table rows from an empty result set', () => {
  const rows = benchmarkTable({
    results: [],
    salaryValueMatches: 0,
    keywordMatches: 0,
    keywordReferences: 0,
  });
  assert.equal(rows.length, 7);
  assert.equal(rows[0].Result, '0/0');
  assert.match(rows[0].Metric, /^Salary amounts/);
  assert.match(rows[3].Metric, /^Keyword response JSON/);
  assert.ok(
    rows.every((row) => Object.keys(row).join(',') === 'Metric,Result'),
  );
});

test('normalizes exact decimal values without rounding or accepting commas', () => {
  assert.equal(decimalKey('9007199254740993.000'), '9007199254740993');
  assert.equal(decimalKey('21.60'), '21.6');
  assert.throws(() => decimalKey('50,000'));
  assert.throws(() => decimalKey('NaN'));
  assert.equal(
    salaryKey([null, '21.60', '21.60', 'hour']),
    salaryKey([null, '21.6', '21.6', 'hour']),
  );
});
test('selects supported reasoning explicitly, not silently claiming off', () => {
  assert.equal(chooseReasoning(['off', 'on'], 'auto'), 'none');
  assert.equal(chooseReasoning(['low', 'medium', 'high'], 'auto'), 'low');
  assert.throws(() => chooseReasoning(['low', 'medium', 'high'], 'none'));
  assert.equal(chooseReasoning([], 'auto'), undefined);
});
test('counts alias groups once and preserves distinct technical terms', () => {
  assert.deepEqual(
    lexicalCoverage(
      ['Node.js', 'node.js', 'C++'],
      [['Node.js', 'NodeJS'], ['C'], ['C++']],
    ),
    { matched: 2, total: 3, missing: [['C']] },
  );
});
test('requires exact source quotation and mention containment', () => {
  assert.equal(quoteCheck('alpha beta', 'alpha', 'alpha beta'), true);
  assert.equal(quoteCheck('alpha beta', 'alpha beta', 'alpha'), false);
  assert.equal(quoteCheck('alpha beta', 'alpha', 'alpha ... beta'), false);
});
test('parses final JSON, never rescues malformed/truncated content', () => {
  assert.deepEqual(parseFinal('```json\n{}\n```'), {});
  assert.throws(() => parseFinal('{'));
  assert.throws(() => parseFinal('<think>hidden</think>{}'));
});
test('validates bounded CLI options and refuses credential input', () => {
  assert.equal(optionsFromArgs(['--dry-run']).dryRun, true);
  assert.throws(() => optionsFromArgs(['--concurrency', '0']));
  assert.throws(() => optionsFromArgs(['--concurrency', '100']));
  assert.throws(() => optionsFromArgs(['--dataset', '.credentials.json']));
  assert.throws(() => optionsFromArgs(['--unknown']));
});
