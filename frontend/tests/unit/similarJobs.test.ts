import { expect, it } from 'vitest';
import {
  parseKeywordIndex,
  selectSimilarIndexes,
} from '../../src/lib/similarJobs';

it('orders numeric score primitives descending, excludes self/zero/unavailable, and breaks ties by ID', () => {
  expect(
    selectSimilarIndexes(
      ['9', '10', '2', '3', '4', '5'],
      [100, 50, 50, 0, null, NaN],
      '9',
    ),
  ).toEqual([2, 1]);
  expect(selectSimilarIndexes(['9', '10', '2'], [100, 50, 50], '9', 1)).toEqual(
    [2],
  );
  expect(selectSimilarIndexes([], [], '0')).toEqual([]);
});
it('validates an empty index and rejects malformed payloads without exposing values', () => {
  const empty = {
    revision: '0'.repeat(64),
    run_id: null,
    total_postings: 0,
    entries: [],
  };
  expect(parseKeywordIndex(empty)).toEqual(empty);
  for (const value of [
    null,
    {},
    [],
    { ...empty, revision: 'bad' },
    { ...empty, total_postings: -1 },
    { ...empty, entries: [null] },
  ]) {
    expect(() => parseKeywordIndex(value)).toThrow(
      'Saved keyword index unavailable',
    );
  }
});
