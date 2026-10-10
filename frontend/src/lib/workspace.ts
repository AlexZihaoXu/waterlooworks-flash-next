import type { components } from './api.generated';
import { inferredCountryName } from './countryInference';
import { groupNumber } from './numberFormat';
import { matchesSelection, matchesAnySelection } from './filterSelection';

// API types are generated from the same OpenAPI served at /doc/openapi.json.
export type Posting = components['schemas']['PostingResponse'];
export type ListingSnapshot = components['schemas']['StoredListing'];
export type RefreshSnapshot = components['schemas']['RefreshSnapshot'];
export type WorkspaceResponse = components['schemas']['WorkspaceResponse'];
export type WorkspaceState =
  | WorkspaceResponse
  | { status: 'disconnected' | 'loading' | 'error' | 'signed-out' };
export type FilterKey =
  'location' | 'country' | 'organization' | 'targeted_disciplines';
export type PostingFilters = Record<FilterKey, readonly string[]>;
export const emptyFilters: PostingFilters = {
  location: [],
  country: [],
  organization: [],
  targeted_disciplines: [],
};
export const filterLabels = {
  organization: 'Organization',
  country: 'Country',
  location: 'City',
  targeted_disciplines: 'Targeted disciplines',
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

export function displayNumber(value: unknown): string {
  return groupNumber(displayValue(value));
}

export function formatCount(
  value: unknown,
  noun: 'application' | 'opening',
): string {
  const text = displayNumber(value);
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

/** Every query word must match a real text field; words may span fields. */
export function matchesSearch(
  values: readonly unknown[],
  query: string,
): boolean {
  const term = query.trim().toLocaleLowerCase();
  if (!term) return true;
  const fields = values
    .filter((value): value is string => typeof value === 'string')
    .map((value) => value.toLocaleLowerCase());
  return term
    .split(/\s+/)
    .every((word) => fields.some((field) => field.includes(word)));
}

export function filterValue(
  posting: Posting,
  key: Exclude<FilterKey, 'targeted_disciplines'>,
): string | null {
  const value =
    key === 'organization'
      ? posting.listing.organization
      : key === 'country'
        ? inferredCountryName(posting.country_inference)
        : posting.location;
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

export function filterOptions(
  postings: readonly Posting[],
  key: FilterKey,
): string[] {
  if (key === 'targeted_disciplines')
    return [
      ...new Set(
        postings.flatMap((posting) => posting.targeted_disciplines ?? []),
      ),
    ].sort((a, b) => a.localeCompare(b));
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
      matchesSearch(
        [
          posting.posting_id,
          posting.listing.title,
          posting.listing.organization,
          posting.location,
          posting.listing.city,
          posting.country,
          inferredCountryName(posting.country_inference),
          posting.country_inference?.state === 'accepted'
            ? posting.country_inference.country_code
            : null,
        ],
        query,
      ) &&
      (Object.keys(filters) as FilterKey[]).every((key) =>
        key === 'targeted_disciplines'
          ? matchesAnySelection(
              posting.targeted_disciplines ?? [],
              filters[key],
            )
          : matchesSelection(filterValue(posting, key), filters[key]),
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
