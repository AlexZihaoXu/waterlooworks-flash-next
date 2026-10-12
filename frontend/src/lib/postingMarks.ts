import type { components } from './api.generated';

// Per-user annotations; the generated contract is the single source of truth.
export type PostingMark = components['schemas']['PostingMark'];
export type MarkState = NonNullable<PostingMark['state']>;
export type PostingMarks = Readonly<Record<string, PostingMark>>;
export type MarkStateFilter = MarkState | 'none';
export type ViewedFilter = 'any' | 'viewed' | 'unviewed';
export type MarkFilters = {
  /** OR within the list; empty means every state. */
  states: readonly MarkStateFilter[];
  viewed: ViewedFilter;
  /** Ignored while `states` explicitly asks for not-interested postings. */
  hideNotInterested: boolean;
};

export const MARK_STATES: readonly MarkState[] = [
  'interested',
  'applied',
  'not_interested',
];
export const markLabels: Record<MarkState, string> = {
  interested: 'Interested',
  applied: 'Applied',
  not_interested: 'Not interested',
};
export const markStateFilterLabels: Record<MarkStateFilter, string> = {
  ...markLabels,
  none: 'Unmarked',
};
export const viewedFilterLabels: Record<ViewedFilter, string> = {
  any: 'All',
  viewed: 'Viewed',
  unviewed: 'Not viewed',
};
export const defaultMarkFilters: MarkFilters = {
  states: [],
  viewed: 'any',
  hideNotInterested: true,
};

export function isMarkState(value: unknown): value is MarkState {
  return MARK_STATES.includes(value as MarkState);
}

export function isMarkStateFilter(value: unknown): value is MarkStateFilter {
  return value === 'none' || isMarkState(value);
}

function hides(filters: MarkFilters) {
  return (
    filters.hideNotInterested && !filters.states.includes('not_interested')
  );
}

export function matchesMarkFilters(
  mark: PostingMark | undefined,
  filters: MarkFilters,
): boolean {
  const state = mark?.state ?? null;
  if (hides(filters) && state === 'not_interested') return false;
  if (filters.states.length && !filters.states.includes(state ?? 'none'))
    return false;
  const viewed = Boolean(mark?.viewed_at);
  if (filters.viewed === 'viewed' && !viewed) return false;
  if (filters.viewed === 'unviewed' && viewed) return false;
  return true;
}

export function filterByMarks<T extends { posting_id: string }>(
  postings: readonly T[],
  marks: PostingMarks,
  filters: MarkFilters,
): T[] {
  return postings.filter((posting) =>
    matchesMarkFilters(marks[posting.posting_id], filters),
  );
}

/** Postings that would match if not-interested ones were shown. */
export function hiddenNotInterested(
  postings: readonly { posting_id: string }[],
  marks: PostingMarks,
  filters: MarkFilters,
): number {
  if (!hides(filters)) return 0;
  const shown = { ...filters, hideNotInterested: false };
  return postings.filter(
    (posting) =>
      marks[posting.posting_id]?.state === 'not_interested' &&
      matchesMarkFilters(marks[posting.posting_id], shown),
  ).length;
}

/** Narrowing selections only; the default hide switch is not "a filter". */
export function markFilterCount(filters: MarkFilters): number {
  return filters.states.length + (filters.viewed === 'any' ? 0 : 1);
}

export function applyMarkState(
  mark: PostingMark | undefined,
  postingId: string,
  state: MarkState | null,
  now: string,
): PostingMark {
  return {
    posting_id: postingId,
    state,
    viewed_at: mark?.viewed_at ?? null,
    updated_at: now,
  };
}

export function applyViewed(
  mark: PostingMark | undefined,
  postingId: string,
  viewed: boolean,
  now: string,
): PostingMark {
  return {
    posting_id: postingId,
    state: mark?.state ?? null,
    viewed_at: viewed ? (mark?.viewed_at ?? now) : null,
    updated_at: now,
  };
}

const isTime = (value: unknown) =>
  value === null ||
  (typeof value === 'string' &&
    value.length <= 64 &&
    !Number.isNaN(Date.parse(value)));

export function parsePostingMark(value: unknown): PostingMark {
  if (!value || typeof value !== 'object')
    throw new Error('Marks unavailable.');
  const data = value as Record<string, unknown>;
  if (
    typeof data.posting_id !== 'string' ||
    !data.posting_id ||
    data.posting_id.length > 64 ||
    !(data.state === null || isMarkState(data.state)) ||
    !isTime(data.viewed_at) ||
    !isTime(data.updated_at)
  )
    throw new Error('Marks unavailable.');
  return {
    posting_id: data.posting_id,
    state: data.state as PostingMark['state'],
    viewed_at: data.viewed_at as string | null,
    updated_at: data.updated_at as string | null,
  };
}

export function parsePostingMarks(value: unknown): Record<string, PostingMark> {
  const marks =
    value && typeof value === 'object' && 'marks' in value ? value.marks : null;
  if (!marks || typeof marks !== 'object' || Array.isArray(marks))
    throw new Error('Marks unavailable.');
  const result: Record<string, PostingMark> = {};
  for (const [key, entry] of Object.entries(marks)) {
    const mark = parsePostingMark(entry);
    if (mark.posting_id !== key) throw new Error('Marks unavailable.');
    result[key] = mark;
  }
  return result;
}
