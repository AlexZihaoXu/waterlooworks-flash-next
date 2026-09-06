import { describe, expect, it } from 'vitest';
import {
  displayValue,
  formatCount,
  filterOptions,
  filterPostings,
  matchesTitle,
  pageNumbers,
} from '../../src/lib/workspace';
import { parseRefresh, parseWorkspace } from '../../src/lib/useWorkspace';

// Generic string/pagination arithmetic and invalid shapes only; no fake postings.
describe('workspace logic', () => {
  it('uses singular and plural count captions without inventing missing counts', () => {
    expect(formatCount(1, 'application')).toBe('1 application');
    expect(formatCount('1', 'opening')).toBe('1 opening');
    expect(formatCount(0, 'application')).toBe('0 applications');
    expect(formatCount(2, 'opening')).toBe('2 openings');
    expect(formatCount(null, 'opening')).toBe('Openings not recorded');
  });
  it('uses case-insensitive title matching and treats missing titles as unknown', () => {
    expect(matchesTitle('Alpha beta', ' BETA ')).toBe(true);
    expect(matchesTitle('Alpha', 'beta')).toBe(false);
    expect(matchesTitle(undefined, 'beta')).toBe(false);
    expect(matchesTitle(null, '')).toBe(true);
  });
  it('does not manufacture filter options or records', () => {
    expect(filterPostings([], '')).toEqual([]);
    expect(filterOptions([], 'country')).toEqual([]);
    expect(filterOptions([], 'location')).toEqual([]);
    expect(filterOptions([], 'organization')).toEqual([]);
  });
  it('keeps pagination bounded for large collections', () => {
    expect(pageNumbers(1, 1)).toEqual([1]);
    expect(pageNumbers(1, 102)).toEqual([1, 2, 3, 4, 5, 'ellipsis', 102]);
    expect(pageNumbers(50, 102)).toEqual([
      1,
      'ellipsis',
      48,
      49,
      50,
      51,
      52,
      'ellipsis',
      102,
    ]);
    expect(pageNumbers(102, 102)).toEqual([
      1,
      'ellipsis',
      98,
      99,
      100,
      101,
      102,
    ]);
    expect(pageNumbers(50, 102, true)).toEqual([
      1,
      'ellipsis',
      50,
      'ellipsis',
      102,
    ]);
  });
  it('keeps absent values distinct from zero and false', () => {
    expect(displayValue(null)).toBe('Not recorded');
    expect(displayValue(undefined)).toBe('Not recorded');
    expect(displayValue(0)).toBe('0');
    expect(displayValue(false)).toBe('No');
  });
  it('rejects invalid HTTP/stream data without rendering source diagnostics', () => {
    for (const value of [null, [], {}, '<html>']) {
      expect(() => parseWorkspace(value)).toThrow(
        'Invalid workspace response.',
      );
      expect(() => parseRefresh(value)).toThrow('Invalid refresh event.');
    }
  });
});
