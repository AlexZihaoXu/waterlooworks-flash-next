import { describe, expect, it } from 'vitest';
import {
  refreshProgress,
  refreshStageLabel,
  refreshStopLabel,
  refreshWaitText,
} from '../../src/lib/refreshProgress';

// Primitive counters only, never fabricated refresh or posting payloads.
describe('refresh phase progress', () => {
  it('switches from finished listing counts to the actual detail queue', () => {
    for (const [stage, stageLabel] of [
      ['details', 'Fetching posting details'],
      ['detail_retry', 'Fetching posting details (waiting to retry)'],
      [
        'detail_rest',
        'Fetching posting details (resting to stay polite to WaterlooWorks)',
      ],
    ]) {
      expect(refreshProgress(stage, [25, 25], [2, 12])).toEqual({
        detailsPhase: true,
        label: 'Detail content progress',
        stageLabel,
        value: 2,
        maxValue: 12,
        isIndeterminate: false,
      });
    }
    expect(refreshProgress('listings', [4, 25], [0, 0])).toEqual({
      detailsPhase: false,
      label: 'Listing refresh progress',
      stageLabel: 'Collecting listings',
      value: 4,
      maxValue: 25,
      isIndeterminate: false,
    });
  });
  it('keeps listing rests and retries on listing progress with polite copy', () => {
    expect(refreshProgress('listing_rest', [4, 25], [0, 0])).toEqual({
      detailsPhase: false,
      label: 'Listing refresh progress',
      stageLabel:
        'Collecting listings (resting to stay polite to WaterlooWorks)',
      value: 4,
      maxValue: 25,
      isIndeterminate: false,
    });
    expect(refreshStageLabel('listing_retry')).toBe(
      'Collecting listings (waiting to retry)',
    );
  });
  it('labels early stops and flags ban-risk warnings', () => {
    expect(refreshStopLabel(null)).toBeNull();
    expect(refreshStopLabel('request_cap')).toBe(
      'Finished partially: per-run WaterlooWorks request cap reached',
    );
    expect(refreshStopLabel('daily_cap')).toMatch(/24-hour/);
    expect(refreshStopLabel('ban_risk')).toBe(
      'Stopped: WaterlooWorks ban-risk warning; refreshes are blocked',
    );
    expect(refreshStopLabel('unknown')).toBeNull();
  });
  it('formats refusal waits coarsely', () => {
    expect(refreshWaitText(30)).toBe('about 1 min');
    expect(refreshWaitText(1200)).toBe('about 20 min');
    expect(refreshWaitText(3600)).toBe('about 1 h');
    expect(refreshWaitText(6 * 3600 - 10)).toBe('about 6 h');
    expect(refreshWaitText(3 * 3600 + 300)).toBe('about 3 h 5 min');
  });
  it('never creates a zero maximum for unavailable totals', () => {
    expect(
      refreshProgress('authentication', [0, null], [0, 0]).isIndeterminate,
    ).toBe(true);
    expect(refreshProgress('details', [25, 25], [0, 0]).maxValue).toBe(1);
  });
  it('maps every backend stage to readable copy, including the Duo wait', () => {
    expect(refreshStageLabel('authentication')).toBe(
      'Signing in to WaterlooWorks · Duo approval may be required',
    );
    expect(
      refreshProgress('authentication', [0, null], [0, 0]).stageLabel,
    ).toBe('Signing in to WaterlooWorks · Duo approval may be required');
    expect(refreshStageLabel('saving')).toBe('Saving listings');
    expect(refreshStageLabel('normalizing')).toBe('Normalizing saved postings');
    expect(refreshStageLabel('analysis')).toBe('Extracting with OpenAI');
    expect(refreshStageLabel('complete')).toBe('Complete');
    expect(refreshStageLabel('stopped')).toBe('Stopped');
    expect(refreshStageLabel('recovery_required')).toBe(
      'Previous refresh was interrupted',
    );
    expect(refreshStageLabel('previous_run')).toBe('Previous refresh');
    expect(refreshStageLabel('idle')).toBe('Idle');
    // Unknown future stages stay readable instead of raw snake_case.
    expect(refreshStageLabel('some_new_stage')).toBe('Some new stage');
  });
});
