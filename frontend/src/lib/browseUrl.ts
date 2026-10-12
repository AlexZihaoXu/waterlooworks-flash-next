import { sortLabels, type PostingSort } from './sorting';
import type { PostingFilters } from './workspace';
import {
  defaultMarkFilters,
  isMarkStateFilter,
  type MarkFilters,
} from './postingMarks';

export type BrowseView = {
  query: string;
  page: number;
  sort: PostingSort;
  filters: PostingFilters;
  /** Per-user mark filters; not-interested postings stay hidden by default. */
  marks: MarkFilters;
};
const filterParameters = {
  organization: 'organization',
  country: 'country',
  location: 'city',
  targeted_disciplines: 'target',
} as const;

export function readBrowseUrl(search: string): BrowseView {
  const params = new URLSearchParams(search);
  const rawPage = params.get('page') ?? '1';
  const page =
    /^[1-9]\d*$/.test(rawPage) && Number.isSafeInteger(Number(rawPage))
      ? Number(rawPage)
      : 1;
  const rawSort = params.get('sort') ?? 'id';
  const rawViewed = params.get('viewed');
  const values = (key: string) => [
    ...new Set(
      params
        .getAll(key)
        .map((value) => value.trim())
        .filter(Boolean),
    ),
  ];
  return {
    query: params.get('q') ?? '',
    page,
    sort: Object.hasOwn(sortLabels, rawSort) ? (rawSort as PostingSort) : 'id',
    filters: {
      organization: values('organization'),
      country: values('country'),
      location: values('city'),
      targeted_disciplines: values('target'),
    },
    marks: {
      states: values('mark').filter(isMarkStateFilter),
      viewed:
        rawViewed === 'viewed' || rawViewed === 'unviewed'
          ? rawViewed
          : defaultMarkFilters.viewed,
      hideNotInterested: !params.getAll('show').includes('not_interested'),
    },
  };
}

export function writeBrowseUrl(href: string, view: BrowseView): string {
  const url = new URL(href);
  for (const key of [
    'q',
    'page',
    'sort',
    ...Object.values(filterParameters),
    'mark',
    'viewed',
    'show',
  ])
    url.searchParams.delete(key);
  if (view.query) url.searchParams.set('q', view.query);
  if (view.page > 1) url.searchParams.set('page', String(view.page));
  if (view.sort !== 'id') url.searchParams.set('sort', view.sort);
  for (const [key, param] of Object.entries(filterParameters)) {
    for (const value of view.filters[key as keyof PostingFilters])
      url.searchParams.append(param, value);
  }
  for (const state of view.marks.states) url.searchParams.append('mark', state);
  if (view.marks.viewed !== 'any')
    url.searchParams.set('viewed', view.marks.viewed);
  if (!view.marks.hideNotInterested)
    url.searchParams.set('show', 'not_interested');
  return url.pathname + url.search + url.hash;
}
