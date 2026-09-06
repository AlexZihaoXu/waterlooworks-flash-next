import type { components } from './api.generated';
import { matchesSelection } from './filterSelection';

// API types are generated from the same OpenAPI served at /doc/openapi.json.
export type Posting = components['schemas']['PostingResponse'];
export type ListingSnapshot = components['schemas']['StoredListing'];
export type ListingRevision = components['schemas']['ListingRevision'];
export type RefreshSnapshot = components['schemas']['RefreshSnapshot'];
export type WorkspaceResponse = components['schemas']['WorkspaceResponse'];
export type WorkspaceState =
  WorkspaceResponse | { status: 'disconnected' | 'loading' | 'error' };
export type FilterKey = 'location' | 'country' | 'organization';
export type PostingFilters = Record<FilterKey, readonly string[]>;
export const emptyFilters: PostingFilters = {
  location: [],
  country: [],
  organization: [],
};
export const filterLabels = {
  organization: 'Organization',
  country: 'Country',
  location: 'City',
} as const;
export const listingFields = [
  ['id', 'Posting ID'],
  ['title', 'Title'],
  ['organization', 'Organization'],
  ['division', 'Division'],
  ['openings', 'Openings'],
  ['city', 'Location'],
  ['level', 'Level'],
  ['application_count', 'Applications'],
  ['deadline', 'Deadline'],
] as const;

export function displayValue(value: unknown): string {
  if (value === undefined || value === null) return 'Not recorded';
  if (typeof value === 'string') return value.trim() || 'Not recorded';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') return String(value);
  // Keep structured source values as escaped text; never render source HTML.
  return JSON.stringify(value) ?? 'Not recorded';
}

export function formatCount(
  value: unknown,
  noun: 'application' | 'opening',
): string {
  const text = displayValue(value);
  if (text === 'Not recorded')
    return `${noun === 'application' ? 'Applications' : 'Openings'} not recorded`;
  const singular =
    value === 1 || (typeof value === 'string' && /^0*1$/.test(value.trim()));
  return `${text} ${noun}${singular ? '' : 's'}`;
}

export function displayTime(value: string | null | undefined): string {
  if (!value) return 'Not recorded';
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return 'Not recorded';
  return (
    new Intl.DateTimeFormat('en-CA', {
      dateStyle: 'medium',
      timeStyle: 'short',
      timeZone: 'UTC',
    }).format(date) + ' UTC'
  );
}

export function matchesTitle(
  title: string | null | undefined,
  query: string,
): boolean {
  const term = query.trim().toLocaleLowerCase();
  return !term || (title ?? '').toLocaleLowerCase().includes(term);
}

export function filterValue(posting: Posting, key: FilterKey): string | null {
  const value =
    key === 'organization'
      ? posting.listing.organization
      : key === 'country'
        ? posting.country
        : posting.location;
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

export function filterOptions(
  postings: readonly Posting[],
  key: FilterKey,
): string[] {
  const options = new Map<string, string>();
  postings.forEach((posting) => {
    const value = filterValue(posting, key);
    if (value) options.set(value.toLocaleLowerCase(), value);
  });
  return [...options.values()].sort((a, b) => a.localeCompare(b));
}

export function filterPostings(
  postings: readonly Posting[],
  query: string,
  filters: PostingFilters = emptyFilters,
): Posting[] {
  return postings.filter(
    (posting) =>
      matchesTitle(posting.listing.title, query) &&
      (Object.keys(filters) as FilterKey[]).every((key) =>
        matchesSelection(filterValue(posting, key), filters[key]),
      ),
  );
}

export function pageNumbers(
  current: number,
  total: number,
  compact = false,
): (number | 'ellipsis')[] {
  if (total <= (compact ? 5 : 7))
    return Array.from({ length: Math.max(0, total) }, (_, index) => index + 1);
  const start = Math.max(1, Math.min(current - 2, total - 4));
  const window = compact
    ? [current]
    : Array.from({ length: 5 }, (_, index) => start + index);
  const included = new Set([1, total, ...window]);
  const sorted = [...included]
    .filter((page) => page >= 1 && page <= total)
    .sort((a, b) => a - b);
  const result: (number | 'ellipsis')[] = [];
  sorted.forEach((page, index) => {
    if (index > 0 && page - sorted[index - 1]! > 1) result.push('ellipsis');
    result.push(page);
  });
  return result;
}
