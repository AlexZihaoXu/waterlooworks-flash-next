import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import {
  displayValue,
  formatCount,
  filterOptions,
  filterPostings,
  matchesSearch,
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
  it('matches case-insensitive terms across any supplied search fields', () => {
    expect(matchesSearch(['Alpha beta'], ' BETA ')).toBe(true);
    expect(matchesSearch(['Alpha', 'Beta', 'Gamma'], ' GAM\tALP ')).toBe(true);
    expect(matchesSearch([null, 'Beta', undefined], 'beta')).toBe(true);
    expect(matchesSearch(['Alpha', 'Beta'], 'alpha absent')).toBe(false);
    expect(matchesSearch(['Alpha', 'Beta'], 'habe')).toBe(false);
    expect(matchesSearch([], ' \n\t ')).toBe(true);
  });
  it('does not search display fallbacks or coerce missing/non-text values', () => {
    const missing = [undefined, null, '', 123, true, { value: 'beta' }];
    for (const query of [
      'beta',
      'undefined',
      'null',
      '123',
      'true',
      'not recorded',
    ]) {
      expect(matchesSearch(missing, query)).toBe(false);
    }
  });
  it('includes posting IDs in the search fields without dropping organization', () => {
    const source = readFileSync('src/lib/workspace.ts', 'utf8');
    const filter = source.slice(
      source.indexOf('export function filterPostings('),
    );
    expect(filter).toMatch(/matchesSearch\(\s*\[\s*posting\.posting_id,/);
    expect(filter).toContain('posting.listing.organization');
    expect(matchesSearch(['001234', 'Alpha'], '1234 alpha')).toBe(true);
  });
  it('does not manufacture filter options or records', () => {
    expect(filterPostings([], '')).toEqual([]);
    expect(filterOptions([], 'country')).toEqual([]);
    expect(filterOptions([], 'location')).toEqual([]);
    expect(filterOptions([], 'organization')).toEqual([]);
    expect(filterOptions([], 'targeted_disciplines')).toEqual([]);
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
