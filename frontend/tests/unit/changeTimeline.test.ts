import { describe, expect, it } from 'vitest';
import { changeTimeline } from '../../src/lib/jobDetail';

describe('change-only timeline', () => {
  it('skips consecutive duplicates but preserves reversions', () => {
    expect(changeTimeline([1, 1, 2, 2, 1], String)).toEqual([1, 2, 1]);
  });
  it('retains the latest observation in an unchanged interval', () => {
    const values = [
      [1, 10],
      [1, 20],
      [2, 30],
    ];
    expect(changeTimeline(values, (v) => String(v[0]))).toEqual([
      [1, 20],
      [2, 30],
    ]);
  });
  it('handles empty and single-state timelines', () => {
    expect(changeTimeline([], String)).toEqual([]);
    expect(changeTimeline([0, 0], String)).toEqual([0]);
  });
});
