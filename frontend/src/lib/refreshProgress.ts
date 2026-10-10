const stageLabels: Record<string, string> = {
  idle: 'Idle',
  authentication: 'Signing in to WaterlooWorks · Duo approval may be required',
  listings: 'Collecting listings',
  saving: 'Saving listings',
  details: 'Fetching posting details',
  detail_retry: 'Fetching posting details (waiting to retry)',
  detail_rest: 'Fetching posting details (scheduled pause)',
  normalizing: 'Normalizing saved postings',
  analysis: 'Extracting with OpenAI',
  complete: 'Complete',
  stopped: 'Stopped',
  recovery_required: 'Previous refresh was interrupted',
  previous_run: 'Previous refresh',
};

/** Readable copy for a backend refresh stage; unknown stages stay legible. */
export function refreshStageLabel(stage: string): string {
  if (Object.hasOwn(stageLabels, stage)) return stageLabels[stage];
  const words = stage.replaceAll('_', ' ').trim();
  return words ? words[0].toUpperCase() + words.slice(1) : 'Unknown stage';
}

export function refreshProgress(
  stage: string,
  listing: readonly [completed: number, total: number | null],
  details: readonly [completed: number, total: number],
  analysis: readonly [completed: number, total: number] = [0, 0],
) {
  if (stage === 'analysis' || stage === 'normalizing')
    return {
      detailsPhase: false,
      stageLabel: refreshStageLabel(stage),
      label: 'OpenAI extraction progress',
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
