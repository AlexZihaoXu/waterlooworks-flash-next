import type { RefreshSnapshot } from '../../src/lib/workspace';

// The backend RefreshSnapshot contract defaults (api_models.RefreshSnapshot),
// used only as a state-machine input. No posting data or provider output.
export function refreshSnapshot(
  overrides: Partial<RefreshSnapshot> = {},
): RefreshSnapshot {
  return {
    run_id: null,
    status: 'idle',
    stage: 'idle',
    pages_completed: 0,
    total_pages: null,
    records_received: 0,
    unique_postings: 0,
    duplicates: 0,
    retry_after: 0,
    details_completed: 0,
    details_total: 0,
    detail_requests: 0,
    detail_retries: 0,
    detail_bursts: 0,
    detail_wait_seconds: 0,
    detail_limited: false,
    analysis_job_id: null,
    analysis_total: 0,
    analysis_completed: 0,
    analysis_accepted: 0,
    analysis_rejected: 0,
    analysis_failed: 0,
    analysis_skipped: 0,
    analysis_cache_hits: 0,
    eta_seconds: null,
    logs: [],
    ...overrides,
  };
}
