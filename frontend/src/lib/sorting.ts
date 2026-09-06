import type { Posting } from './workspace';
import { compareRemaining, parseDeadline } from './deadlines';

export const sortLabels = {
  id: 'Default (posting ID)',
  newest: 'Recently added',
  remaining: 'Time remaining',
  'applications-desc': 'Most applications',
  'openings-desc': 'Most openings',
  organization: 'Organization',
} as const;
export type PostingSort = keyof typeof sortLabels;
type Kind = 'text' | 'count' | 'date';

function sortValue(value: unknown, kind: Kind): string | number | null {
  if (kind === 'count') {
    const number =
      typeof value === 'number'
        ? value
        : typeof value === 'string' && /^\d+$/.test(value.trim())
          ? Number(value.trim())
          : NaN;
    return Number.isSafeInteger(number) && number >= 0 ? number : null;
  }
  if (typeof value !== 'string' || !value.trim()) return null;
  if (kind === 'text') return value.trim();
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : null;
}

export function compareSortValues(
  left: unknown,
  right: unknown,
  kind: Kind,
  descending = false,
): number {
  const a = sortValue(left, kind);
  const b = sortValue(right, kind);
  // Unknown values always sort last, including in descending order.
  if (a === null) return b === null ? 0 : 1;
  if (b === null) return -1;
  const comparison =
    typeof a === 'number' && typeof b === 'number'
      ? a - b
      : String(a).localeCompare(String(b), 'en', { sensitivity: 'base' });
  return descending ? -comparison : comparison;
}

export function sortPostings(
  postings: readonly Posting[],
  sort: PostingSort,
  now = Date.now(),
): Posting[] {
  // Parse once per record, not on each comparator call.
  const deadlines =
    sort === 'remaining'
      ? new Map(
          postings.map((posting) => [
            posting.posting_id,
            parseDeadline(posting.listing.deadline),
          ]),
        )
      : new Map<string, number | null>();
  return [...postings].sort((a, b) => {
    let comparison: number;
    switch (sort) {
      case 'id':
        comparison = 0;
        break;
      case 'remaining':
        comparison = compareRemaining(
          deadlines.get(a.posting_id) ?? null,
          deadlines.get(b.posting_id) ?? null,
          now,
        );
        break;
      case 'newest':
        comparison = compareSortValues(
          a.first_seen_at,
          b.first_seen_at,
          'date',
          true,
        );
        break;
      case 'organization':
        comparison = compareSortValues(
          a.listing.organization,
          b.listing.organization,
          'text',
        );
        break;
      case 'applications-desc':
        comparison = compareSortValues(
          a.listing.application_count,
          b.listing.application_count,
          'count',
          true,
        );
        break;
      case 'openings-desc':
        comparison = compareSortValues(
          a.listing.openings,
          b.listing.openings,
          'count',
          true,
        );
        break;
    }
    return (
      comparison ||
      a.posting_id.localeCompare(b.posting_id, 'en', { numeric: true })
    );
  });
}
