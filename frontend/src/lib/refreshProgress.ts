const stageLabels: Record<string, string> = {
  idle: 'Idle',
  authentication: 'Signing in to WaterlooWorks · Duo approval may be required',
  listings: 'Collecting listings',
  listing_rest: 'Collecting listings (resting to stay polite to WaterlooWorks)',
  listing_retry: 'Collecting listings (waiting to retry)',
  saving: 'Saving listings',
  details: 'Fetching posting details',
  detail_retry: 'Fetching posting details (waiting to retry)',
  detail_rest:
    'Fetching posting details (resting to stay polite to WaterlooWorks)',
  normalizing: 'Normalizing saved postings',
  analysis: 'Extracting with AI',
  complete: 'Complete',
  stopped: 'Stopped',
  recovery_required: 'Previous refresh was interrupted',
  previous_run: 'Previous refresh',
};

/** Readable copy for a backend refresh stage; unknown stages stay legible. */
export function refreshStageLabel(
  stage: string,
  provider?: string | null,
): string {
  if (stage === 'analysis' && provider) return `Extracting with ${provider}`;
  if (Object.hasOwn(stageLabels, stage)) return stageLabels[stage];
  const words = stage.replaceAll('_', ' ').trim();
  return words ? words[0].toUpperCase() + words.slice(1) : 'Unknown stage';
}

export function refreshProgress(
  stage: string,
  listing: readonly [completed: number, total: number | null],
  details: readonly [completed: number, total: number],
  analysis: readonly [completed: number, total: number] = [0, 0],
  provider?: string | null,
) {
  if (stage === 'analysis' || stage === 'normalizing')
    return {
      detailsPhase: false,
      stageLabel: refreshStageLabel(stage, provider),
      label: 'AI extraction progress',
      value: stage === 'normalizing' ? 0 : analysis[0],
      maxValue: Math.max(1, analysis[1]),
      isIndeterminate: stage === 'normalizing' || !analysis[1],
    };
  const detailsPhase = stage.startsWith('detail');
  const [value, total] = detailsPhase ? details : listing;
  return {
    detailsPhase,
    stageLabel: refreshStageLabel(stage),
    label: detailsPhase
      ? 'Detail content progress'
      : 'Listing refresh progress',
    value,
    maxValue: Math.max(1, total ?? 1),
    isIndeterminate: !total,
  };
}

const stopLabels: Record<string, string> = {
  request_cap: 'Finished partially: per-run WaterlooWorks request cap reached',
  daily_cap:
    'Finished partially: rolling 24-hour WaterlooWorks request budget reached',
  ban_risk: 'Stopped: WaterlooWorks ban-risk warning; refreshes are blocked',
};

/** Readable copy for why a run stopped early, or null when it did not. */
export function refreshStopLabel(
  stop: string | null | undefined,
): string | null {
  if (!stop) return null;
  return Object.hasOwn(stopLabels, stop) ? stopLabels[stop] : null;
}

/** Coarse, honest wait text ("about 12 min", "about 3 h 5 min"). */
export function refreshWaitText(seconds: number): string {
  if (seconds < 3600)
    return `about ${Math.max(1, Math.ceil(seconds / 60))} min`;
  let hours = Math.floor(seconds / 3600);
  let minutes = Math.ceil((seconds % 3600) / 60);
  if (minutes === 60) {
    hours += 1;
    minutes = 0;
  }
  return minutes ? `about ${hours} h ${minutes} min` : `about ${hours} h`;
}
