import assert from 'node:assert/strict';
import test from 'node:test';
import {
  countryCode,
  countryOutputSchema,
  exactEvidence,
  boundedConfidence,
  countryInput,
  locationAmbiguity,
} from '../country-lib.mjs';

test('output schema forces complete evidence keys including prefixes and colons', () => {
  const fields = { 'listing:city': 'x', 'content:Job - Province/State:': 'y' };
  assert.deepEqual(
    countryOutputSchema(fields).properties.evidence.items.properties.field.enum,
    Object.keys(fields),
  );
});
test('countryCode accepts only assigned ISO 3166-1 alpha-2 codes', () => {
  assert.equal(countryCode('CA'), 'CA');
  for (const value of ['ZZ', 'UK', 'ca', '', null, ['CA', 'US']])
    assert.equal(countryCode(value), null);
});
test('confidence is finite, bounded and never coerced', () => {
  for (const value of [-1, 1.1, NaN, Infinity, '0.9', null])
    assert.equal(boundedConfidence(value), null);
  assert.equal(boundedConfidence(0.85), 0.85);
});
test('evidence resolves one exact quote in code points, not generated offsets', () => {
  assert.deepEqual(exactEvidence('😀 abc', 'abc'), { start: 2, end: 5 });
  assert.equal(exactEvidence('abc abc', 'abc'), null);
  assert.equal(exactEvidence('abc', 'ABC'), null);
  assert.equal(exactEvidence('abc', ''), null);
});
test('narrow work-location ambiguity guards retain country-scoped remote locations', () => {
  for (const value of ['worldwide', 'Remote - worldwide', 'anywhere', 'London'])
    assert.equal(locationAmbiguity([value]), 'ambiguous_location');
  for (const value of [
    'anywhere in Canada',
    'London, Ontario',
    'Remote (Canada only)',
  ])
    assert.equal(locationAmbiguity([value]), null);
  assert.equal(locationAmbiguity(['London', 'Ontario']), null);
  assert.equal(locationAmbiguity(['Our customers are worldwide']), null);
});
test('empty input stays empty and source text is never truncated', () => {
  assert.deepEqual(countryInput({}, {}), {
    fields: {},
    reason: 'no_location_context',
  });
  const long = 'a'.repeat(25000);
  assert.equal(countryInput({ city: long }, {}).reason, 'input_too_large');
});
