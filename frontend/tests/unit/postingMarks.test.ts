import { describe, expect, it } from 'vitest';
import {
  applyMarkState,
  applyViewed,
  defaultMarkFilters,
  filterByMarks,
  hiddenNotInterested,
  markFilterCount,
  parsePostingMark,
  parsePostingMarks,
  type PostingMark,
} from '../../src/lib/postingMarks';

// Generic identifiers and timestamps only; no posting content.
const at = '2026-01-01T00:00:00+00:00';
const mark = (
  posting_id: string,
  state: PostingMark['state'],
  viewed_at: string | null = null,
): PostingMark => ({ posting_id, state, viewed_at, updated_at: at });
const items = ['a', 'b', 'c', 'd', 'e'].map((posting_id) => ({ posting_id }));
const marks = {
  a: mark('a', 'interested'),
  b: mark('b', 'applied', at),
  c: mark('c', 'not_interested', at),
  d: mark('d', null, at),
};
const ids = (list: readonly { posting_id: string }[]) =>
  list.map((item) => item.posting_id);

describe('posting mark filters', () => {
  it('hides only not-interested postings by default', () => {
    expect(defaultMarkFilters).toEqual({
      states: [],
      viewed: 'any',
      hideNotInterested: true,
    });
    expect(ids(filterByMarks(items, marks, defaultMarkFilters))).toEqual([
      'a',
      'b',
      'd',
      'e',
    ]);
    expect(
      ids(
        filterByMarks(items, marks, {
          ...defaultMarkFilters,
          hideNotInterested: false,
        }),
      ),
    ).toEqual(['a', 'b', 'c', 'd', 'e']);
  });

  it('matches any selected state, with none meaning unmarked', () => {
    expect(
      ids(
        filterByMarks(items, marks, {
          ...defaultMarkFilters,
          states: ['interested', 'none'],
        }),
      ),
    ).toEqual(['a', 'd', 'e']);
  });

  it('lets an explicit not-interested selection override hiding', () => {
    expect(
      ids(
        filterByMarks(items, marks, {
          ...defaultMarkFilters,
          states: ['not_interested'],
        }),
      ),
    ).toEqual(['c']);
  });

  it('filters viewed and unviewed postings', () => {
    const show = { ...defaultMarkFilters, hideNotInterested: false };
    expect(
      ids(filterByMarks(items, marks, { ...show, viewed: 'viewed' })),
    ).toEqual(['b', 'c', 'd']);
    expect(
      ids(filterByMarks(items, marks, { ...show, viewed: 'unviewed' })),
    ).toEqual(['a', 'e']);
  });

  it('counts postings hidden only by the not-interested switch', () => {
    expect(hiddenNotInterested(items, marks, defaultMarkFilters)).toBe(1);
    expect(
      hiddenNotInterested(items, marks, {
        ...defaultMarkFilters,
        viewed: 'unviewed',
      }),
    ).toBe(0);
    expect(
      hiddenNotInterested(items, marks, {
        ...defaultMarkFilters,
        hideNotInterested: false,
      }),
    ).toBe(0);
  });

  it('counts narrowing filters but not the default hide switch', () => {
    expect(markFilterCount(defaultMarkFilters)).toBe(0);
    expect(
      markFilterCount({ ...defaultMarkFilters, hideNotInterested: false }),
    ).toBe(0);
    expect(
      markFilterCount({
        states: ['applied', 'none'],
        viewed: 'unviewed',
        hideNotInterested: true,
      }),
    ).toBe(3);
  });
});

describe('optimistic mark updates', () => {
  it('sets or clears a state while keeping the view time', () => {
    expect(applyMarkState(undefined, 'x', 'applied', at)).toEqual({
      posting_id: 'x',
      state: 'applied',
      viewed_at: null,
      updated_at: at,
    });
    expect(applyMarkState(mark('x', 'applied', at), 'x', null, at)).toEqual(
      mark('x', null, at),
    );
  });

  it('keeps the first view time and clears it when unviewed', () => {
    const later = '2026-02-01T00:00:00+00:00';
    expect(applyViewed(mark('x', null, at), 'x', true, later).viewed_at).toBe(
      at,
    );
    expect(applyViewed(undefined, 'x', true, later).viewed_at).toBe(later);
    expect(applyViewed(mark('x', 'applied', at), 'x', false, later)).toEqual({
      posting_id: 'x',
      state: 'applied',
      viewed_at: null,
      updated_at: later,
    });
  });
});

describe('mark response parsing', () => {
  it('accepts the documented shape keyed by posting ID', () => {
    expect(parsePostingMarks({ marks: { a: marks.a, b: marks.b } })).toEqual({
      a: marks.a,
      b: marks.b,
    });
    expect(parsePostingMark(marks.c)).toEqual(marks.c);
  });

  it('rejects unknown states, mismatched keys and malformed values', () => {
    expect(() => parsePostingMark({ ...marks.a, state: 'starred' })).toThrow();
    expect(() => parsePostingMark({ ...marks.a, viewed_at: 5 })).toThrow();
    expect(() => parsePostingMark({ ...marks.a, posting_id: '' })).toThrow();
    expect(() => parsePostingMarks({ marks: { z: marks.a } })).toThrow();
    expect(() => parsePostingMarks({ marks: [] })).toThrow();
    expect(() => parsePostingMarks(null)).toThrow();
  });
});
