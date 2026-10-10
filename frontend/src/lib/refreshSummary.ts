import { refreshStageLabel } from './refreshProgress';
import { displayNumber, type RefreshSnapshot } from './workspace';

const shortStages: Record<string, string> = {
  authentication: 'Signing in',
  listings: 'Listings',
  listing_rest: 'Listings',
  listing_retry: 'Listings',
  saving: 'Saving',
  details: 'Details',
  detail_retry: 'Details',
  detail_rest: 'Details',
  normalizing: 'Normalizing',
  analysis: 'Extracting',
};

const count = (completed: number, total: number | null | undefined) =>
  `${displayNumber(completed)}/${total ? displayNumber(total) : '?'}`;

/** One short phone status ("Extracting · 650/664"); never the provider name. */
export function refreshShortStatus(refresh: RefreshSnapshot): string {
  const stage = refresh.stage;
  const label = Object.hasOwn(shortStages, stage)
    ? shortStages[stage]
    : refreshStageLabel(stage);
  if (stage === 'analysis')
    return `${label} · ${count(refresh.analysis_completed ?? 0, refresh.analysis_total)}`;
  if (stage.startsWith('detail'))
    return `${label} · ${count(refresh.details_completed, refresh.details_total)}`;
  if (stage.startsWith('listing'))
    return `${label} · ${count(refresh.pages_completed, refresh.total_pages)}`;
  return label;
}

export type RefreshStat = { label: string; value: string };

/** Labeled counters for the current refresh phase. */
export function refreshStats(refresh: RefreshSnapshot): RefreshStat[] {
  const stat = (label: string, value: unknown): RefreshStat => ({
    label,
    value: displayNumber(value),
  });
  const pair = (label: string, done: number, total: unknown) => ({
    label,
    value: `${displayNumber(done)} / ${displayNumber(total)}`,
  });
  if (refresh.stage === 'analysis' || refresh.stage === 'normalizing')
    return [
      pair(
        'Processed',
        refresh.analysis_completed ?? 0,
        refresh.analysis_total ?? 0,
      ),
      stat('Accepted', refresh.analysis_accepted ?? 0),
      stat('Rejected', refresh.analysis_rejected ?? 0),
      stat('Failed', refresh.analysis_failed ?? 0),
      stat('Skipped', refresh.analysis_skipped ?? 0),
      stat('Cache hits', refresh.analysis_cache_hits ?? 0),
    ];
  const wait =
    refresh.detail_wait_seconds > 0
      ? [
          {
            label: 'Scheduled wait',
            value: `${refresh.detail_wait_seconds.toFixed(1)}s`,
          },
        ]
      : [];
  const requests = {
    label: 'WaterlooWorks requests',
    value:
      displayNumber(refresh.ww_requests ?? 0) +
      (refresh.ww_request_budget != null
        ? ` / ${displayNumber(refresh.ww_request_budget)}`
        : ''),
  };
  if (refresh.stage.startsWith('detail'))
    return [
      pair('Detail contents', refresh.details_completed, refresh.details_total),
      stat('Requests', refresh.detail_requests),
      stat('Retries', refresh.detail_retries),
      stat('Rest breaks', refresh.detail_bursts),
      ...wait,
      ...(refresh.detail_limited
        ? [{ label: 'Server cap', value: 'Reached' }]
        : []),
      requests,
    ];
  return [
    pair('Pages', refresh.pages_completed, refresh.total_pages ?? 'unknown'),
    stat('Postings', refresh.unique_postings),
    stat('Records', refresh.records_received),
    stat('Duplicates', refresh.duplicates),
    ...wait,
    requests,
  ];
}
