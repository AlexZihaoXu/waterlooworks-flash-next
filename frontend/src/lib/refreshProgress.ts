export function refreshProgress(
  stage: string,
  listing: readonly [completed: number, total: number | null],
  details: readonly [completed: number, total: number],
  analysis: readonly [completed: number, total: number] = [0, 0],
) {
  if (stage === 'analysis' || stage === 'normalizing')
    return {
      detailsPhase: false,
      stageLabel:
        stage === 'normalizing'
          ? 'Normalizing saved postings'
          : 'Extracting with local LLM',
      label: 'Local extraction progress',
      value: stage === 'normalizing' ? 0 : analysis[0],
      maxValue: Math.max(1, analysis[1]),
      isIndeterminate: stage === 'normalizing' || !analysis[1],
    };
  const detailsPhase = stage.startsWith('detail');
  const [value, total] = detailsPhase ? details : listing;
  return {
    detailsPhase,
    stageLabel: detailsPhase
      ? 'Fetching posting details'
      : stage.replaceAll('_', ' '),
    label: detailsPhase
      ? 'Detail content progress'
      : 'Listing refresh progress',
    value,
    maxValue: Math.max(1, total ?? 1),
    isIndeterminate: !total,
  };
}
