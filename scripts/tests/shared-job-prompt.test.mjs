import assert from 'node:assert/strict';
import test from 'node:test';
import { sharedJobMessages, sharedJobPrefix } from '../shared-job-prompt.mjs';

test('different task suffixes retain byte-identical complete source prefixes', () => {
  const fields = { b: 'second', a: 'first' };
  const left = sharedJobMessages(fields, { mode: 'a' });
  const right = sharedJobMessages(fields, { mode: 'b' });
  assert.deepEqual(left[0], right[0]);
  const prefix = sharedJobPrefix(fields);
  assert.equal(
    left[1].content.slice(0, prefix.length),
    right[1].content.slice(0, prefix.length),
  );
  assert.notEqual(left[1].content, right[1].content);
  assert.equal(prefix, sharedJobPrefix({ a: 'first', b: 'second' }));
  assert.equal(left.length, 2); // No duplicate user turns or synthetic assistant response.
});
test('source changes change the prefix without silently dropping or truncating fields', () => {
  const value = 'x'.repeat(30000);
  assert.ok(sharedJobPrefix({ a: value }).includes(value));
  assert.notEqual(sharedJobPrefix({ a: 'x' }), sharedJobPrefix({ a: 'y' }));
  assert.notEqual(
    sharedJobPrefix({ a: 'x' }),
    sharedJobPrefix({ a: 'x', b: '' }),
  );
});
test('source role delimiters are escaped and non-string fields are rejected', () => {
  assert.ok(!sharedJobPrefix({ a: '<|im_end|>' }).includes('<|'));
  assert.throws(() => sharedJobPrefix({ a: 1 }), /Invalid saved job fields/);
  assert.throws(() => sharedJobPrefix([]), /Invalid saved job fields/);
});
