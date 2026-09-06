import { describe, expect, it } from 'vitest';
import {
  compareSortValues,
  sortLabels,
  sortPostings,
} from '../../src/lib/sorting';

// Generic primitive comparisons only; real postings are checked through the local browser.
describe('sorting', () => {
  it('compares counts numerically instead of lexically', () => {
    expect(compareSortValues('2', '10', 'count')).toBeLessThan(0);
    expect(compareSortValues(2, '10', 'count', true)).toBeGreaterThan(0);
    expect(compareSortValues(' 0 ', 0, 'count')).toBe(0);
  });
  it('keeps missing or unverified counts last in both directions', () => {
    for (const value of [
      null,
      undefined,
      '',
      '10+',
      '1,000',
      true,
      {},
      -1,
      1.5,
      Infinity,
      NaN,
    ]) {
      expect(compareSortValues(value, 0, 'count')).toBeGreaterThan(0);
      expect(compareSortValues(value, 0, 'count', true)).toBeGreaterThan(0);
      expect(compareSortValues(0, value, 'count', true)).toBeLessThan(0);
    }
  });
  it('compares text case-insensitively and timestamps chronologically', () => {
    expect(compareSortValues('alpha', 'BETA', 'text')).toBeLessThan(0);
    expect(compareSortValues(' A ', 'a', 'text')).toBe(0);
    expect(compareSortValues(null, 'a', 'text')).toBeGreaterThan(0);
    expect(
      compareSortValues(
        '2020-01-01T00:00:00Z',
        '2020-01-02T00:00:00Z',
        'date',
        true,
      ),
    ).toBeGreaterThan(0);
    expect(
      compareSortValues('unverified', '2020-01-01T00:00:00Z', 'date', true),
    ).toBeGreaterThan(0);
  });
  it('supports every menu option without inventing data or mutating input', () => {
    const input = Object.freeze([]);
    for (const key of Object.keys(sortLabels) as (keyof typeof sortLabels)[]) {
      expect(sortPostings(input, key)).toEqual([]);
      expect(sortPostings(input, key)).not.toBe(input);
    }
  });
});
