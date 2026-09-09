import assert from 'node:assert/strict';
import test from 'node:test';
import {
  parsePayQuote,
  locateTerm,
  deduplicateTerms,
} from '../extraction-lib.mjs';

test('reads exact base-rate expressions without currency/period guesses', () => {
  assert.equal(parsePayQuote('CAD $8,400/month')[0].minimum, '8400');
  assert.equal(parsePayQuote('$50,000 to $60,000')[0].period, null);
  assert.equal(parsePayQuote('$50,000 to $60,000')[0].currency, null);
  assert.equal(parsePayQuote('65,000 INR/month')[0].currency, 'INR');
  assert.equal(parsePayQuote('$2,461/week USD')[0].currency, 'USD');
  assert.equal(parsePayQuote('CAD $45-55 / hour')[0].period, 'hour');
  assert.equal(parsePayQuote('$55k annually')[0].minimum, '55000');
  assert.equal(parsePayQuote('$23.67 - $26.30* per hour')[0].period, 'hour');
});
test('keeps repeated rates as separate observations and rejects non-pay figures', () => {
  assert.equal(parsePayQuote('$18.84/hour, $20.16/hour').length, 2);
  assert.equal(parsePayQuote('Revenue of $50,000 per year').length, 0);
  assert.equal(parsePayQuote('a $500 monthly housing stipend').length, 0);
  assert.equal(parsePayQuote('a 35 hour work week').length, 0);
  assert.equal(parsePayQuote('USD$40/hour (CAD$55+/hour)').length, 1);
  assert.equal(parsePayQuote('$55+/hour')[0].bound_type, 'minimum');
  assert.equal(parsePayQuote('$55+/hour')[0].maximum, null);
  assert.equal(parsePayQuote('$26-$22/hour').length, 0);
  assert.equal(parsePayQuote('$0.1 million per year').length, 0);
});
test('records exact whole-term spans and deduplicates without merging C/C++', () => {
  assert.deepEqual(locateTerm('C/C++ and SQL', 'C++'), [{ start: 2, end: 5 }]);
  assert.deepEqual(locateTerm('TypeScript', 'Script'), []);
  assert.deepEqual(locateTerm('C++', 'C'), []);
  assert.deepEqual(deduplicateTerms(['Node.js', 'Node.js', 'C', 'C++']), [
    'Node.js',
    'C',
    'C++',
  ]);
});
