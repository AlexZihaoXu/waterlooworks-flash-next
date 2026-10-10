import { describe, expect, it } from 'vitest';
import { refreshShortStatus, refreshStats } from '../../src/lib/refreshSummary';
import { refreshSnapshot } from '../support/refreshSnapshot';

// Contract-default snapshots with primitive counters; no posting or provider data.
describe('compact refresh summary', () => {
  it('keeps the phone status label short and never includes the provider', () => {
    const analysis = refreshSnapshot({
      status: 'running',
      stage: 'analysis',
      analysis_provider: 'A very long provider name / with-a-model-id',
      analysis_completed: 650,
      analysis_total: 664,
    });
    expect(refreshShortStatus(analysis)).toBe('Extracting · 650/664');
    expect(
      refreshShortStatus(
        refreshSnapshot({ stage: 'normalizing', analysis_total: 0 }),
      ),
    ).toBe('Normalizing');
  });

  it('labels each refresh phase tersely with its own counter', () => {
    expect(
      refreshShortStatus(
        refreshSnapshot({
          stage: 'listings',
          pages_completed: 3,
          total_pages: 10,
        }),
      ),
    ).toBe('Listings · 3/10');
    expect(
      refreshShortStatus(
        refreshSnapshot({ stage: 'listing_rest', pages_completed: 3 }),
      ),
    ).toBe('Listings · 3/?');
    expect(
      refreshShortStatus(
        refreshSnapshot({
          stage: 'detail_retry',
          details_completed: 1200,
          details_total: 1500,
        }),
      ),
    ).toBe('Details · 1,200/1,500');
    expect(
      refreshShortStatus(refreshSnapshot({ stage: 'authentication' })),
    ).toBe('Signing in');
    expect(refreshShortStatus(refreshSnapshot({ stage: 'saving' }))).toBe(
      'Saving',
    );
    expect(refreshShortStatus(refreshSnapshot({ stage: 'odd_stage' }))).toBe(
      'Odd stage',
    );
  });

  it('lists AI extraction counters as labeled numbers', () => {
    expect(
      refreshStats(
        refreshSnapshot({
          stage: 'analysis',
          analysis_completed: 650,
          analysis_total: 664,
          analysis_accepted: 2,
          analysis_rejected: 8,
          analysis_failed: 0,
          analysis_skipped: 640,
          analysis_cache_hits: 0,
        }),
      ),
    ).toEqual([
      { label: 'Processed', value: '650 / 664' },
      { label: 'Accepted', value: '2' },
      { label: 'Rejected', value: '8' },
      { label: 'Failed', value: '0' },
      { label: 'Skipped', value: '640' },
      { label: 'Cache hits', value: '0' },
    ]);
  });

  it('lists detail and listing counters with WaterlooWorks request usage', () => {
    expect(
      refreshStats(
        refreshSnapshot({
          stage: 'details',
          details_completed: 4,
          details_total: 12,
          detail_requests: 5,
          detail_retries: 1,
          detail_bursts: 2,
          detail_wait_seconds: 1.25,
          detail_limited: true,
          ww_requests: 30,
          ww_request_budget: 400,
        }),
      ),
    ).toEqual([
      { label: 'Detail contents', value: '4 / 12' },
      { label: 'Requests', value: '5' },
      { label: 'Retries', value: '1' },
      { label: 'Rest breaks', value: '2' },
      { label: 'Scheduled wait', value: '1.3s' },
      { label: 'Server cap', value: 'Reached' },
      { label: 'WaterlooWorks requests', value: '30 / 400' },
    ]);
    expect(
      refreshStats(
        refreshSnapshot({
          stage: 'listings',
          pages_completed: 2,
          unique_postings: 40,
          records_received: 41,
          duplicates: 1,
          ww_requests: 3,
        }),
      ),
    ).toEqual([
      { label: 'Pages', value: '2 / unknown' },
      { label: 'Postings', value: '40' },
      { label: 'Records', value: '41' },
      { label: 'Duplicates', value: '1' },
      { label: 'WaterlooWorks requests', value: '3' },
    ]);
  });
});
