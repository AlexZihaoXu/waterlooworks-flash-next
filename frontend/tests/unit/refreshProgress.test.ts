import { describe, expect, it } from 'vitest';
import { refreshProgress } from '../../src/lib/refreshProgress';

// Primitive counters only, never fabricated refresh or posting payloads.
describe('refresh phase progress', () => {
  it('switches from finished listing counts to the actual detail queue', () => {
    for (const stage of ['details', 'detail_retry', 'detail_rest']) {
      expect(refreshProgress(stage, [25, 25], [2, 12])).toEqual({
        detailsPhase: true,
        label: 'Detail content progress',
        stageLabel: 'Fetching posting details',
        value: 2,
        maxValue: 12,
        isIndeterminate: false,
      });
    }
    expect(refreshProgress('listings', [4, 25], [0, 0])).toEqual({
      detailsPhase: false,
      label: 'Listing refresh progress',
      stageLabel: 'listings',
      value: 4,
      maxValue: 25,
      isIndeterminate: false,
    });
  });
  it('never creates a zero maximum for unavailable totals', () => {
    expect(
      refreshProgress('authentication', [0, null], [0, 0]).isIndeterminate,
    ).toBe(true);
    expect(refreshProgress('details', [25, 25], [0, 0]).maxValue).toBe(1);
  });
});
