import type { components } from './api.generated';
import {
  prepareKeywordProfile,
  prepareKeywordSearch,
  scoreKeywordCoverageBatch,
} from './keywordCoverage';

export type KeywordIndex = components['schemas']['KeywordIndexResponse'];
export function parseKeywordIndex(value: unknown): KeywordIndex {
  const record = (v: unknown): v is Record<string, unknown> =>
    typeof v === 'object' && v !== null && !Array.isArray(v);
  const hash = (v: unknown) =>
    typeof v === 'string' && /^[a-f0-9]{64}$/.test(v);
  if (
    !record(value) ||
    !hash(value.revision) ||
    (value.run_id !== null && typeof value.run_id !== 'string') ||
    !Number.isSafeInteger(value.total_postings) ||
    Number(value.total_postings) < 0 ||
    !Array.isArray(value.entries) ||
    value.entries.length > 10000
  )
    throw new Error('Saved keyword index unavailable');
  const ids = new Set<string>();
  for (const entry of value.entries) {
    if (
      !record(entry) ||
      typeof entry.posting_id !== 'string' ||
      !/^\d+$/.test(entry.posting_id) ||
      ids.has(entry.posting_id) ||
      !hash(entry.content_hash) ||
      typeof entry.partial !== 'boolean' ||
      !Array.isArray(entry.fields) ||
      !entry.fields.every((v) => typeof v === 'string') ||
      !Array.isArray(entry.keywords) ||
      !entry.keywords.every(
        (v) => typeof v === 'string' && v.length > 0 && v.length <= 120,
      )
    )
      throw new Error('Saved keyword index unavailable');
    ids.add(entry.posting_id);
  }
  if (value.entries.length > Number(value.total_postings))
    throw new Error('Saved keyword index unavailable');
  return value as KeywordIndex;
}

export function selectSimilarIndexes(
  ids: readonly string[],
  scores: readonly (number | null)[],
  sourceId: string,
  limit = 12,
): number[] {
  return ids
    .map((_, i) => i)
    .filter(
      (i) =>
        ids[i] !== sourceId &&
        scores[i] !== null &&
        Number.isFinite(scores[i]) &&
        scores[i]! > 0,
    )
    .sort(
      (a, b) =>
        scores[b]! - scores[a]! ||
        ids[a].localeCompare(ids[b], 'en', { numeric: true }) ||
        ids[a].localeCompare(ids[b]),
    )
    .slice(0, limit);
}

export function buildSimilarSearch(index: KeywordIndex) {
  const profiles = index.entries.map((entry) =>
    prepareKeywordProfile(entry.fields, entry.keywords),
  );
  return {
    revision: index.revision,
    total: index.total_postings,
    entries: index.entries.map(({ posting_id, content_hash, partial }) => ({
      posting_id,
      content_hash,
      partial,
    })),
    search: prepareKeywordSearch(profiles),
  };
}
export type SimilarSearch = ReturnType<typeof buildSimilarSearch>;
export function rankSimilarJobs(
  text: string,
  sourceId: string,
  corpus: SimilarSearch,
) {
  const start = performance.now();
  const scores = scoreKeywordCoverageBatch(text, corpus.search);
  const ids = corpus.entries.map((entry) => entry.posting_id);
  const selected = selectSimilarIndexes(
    ids,
    scores.map((score) => score.score),
    sourceId,
  );
  return {
    revision: corpus.revision,
    total: Math.max(0, corpus.total - 1),
    indexed: scores.filter(
      (score, i) => ids[i] !== sourceId && score.score !== null,
    ).length,
    positive: scores.filter(
      (score, i) =>
        ids[i] !== sourceId && score.score !== null && score.score > 0,
    ).length,
    items: selected.map((i) => ({ ...corpus.entries[i], ...scores[i] })),
    scoringMs: performance.now() - start,
  };
}
export type SimilarResult = ReturnType<typeof rankSimilarJobs>;
export type SimilarJobsState =
  | { status: 'idle' | 'loading' | 'error' }
  | { status: 'ready'; result: SimilarResult };
export type SimilarRequest =
  | { requestId: number; sourceId: string; text: string }
  | { requestId: number; cancel: true };
export type SimilarResponse = { requestId: number; state: SimilarJobsState };
