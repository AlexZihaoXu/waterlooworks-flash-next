import type { components } from './api.generated';
import { groupNumber } from './numberFormat';

export type JobDetail = components['schemas']['JobDetailResponse'];
export type JobAnalysis = components['schemas']['PostingAnalysisResponse'];
export type JobHistory = components['schemas']['PostingHistoryResponse'];
export type JobDetailView = JobDetail & {
  analysis: JobAnalysis;
  history: JobHistory;
};
export function extractionStatusLabel(
  state: components['schemas']['ExtractionStatus']['state'],
): string {
  return {
    accepted: 'Accepted saved extraction',
    empty: 'Accepted extraction; no usable output',
    partial: 'Partial extraction; some output was not accepted',
    rejected: 'Rejected extraction; no accepted output',
    failed: 'Extraction failed',
    stale: 'Saved source or extraction identity changed; older output hidden',
    pending: 'Pending extraction; unfinished output hidden',
    not_requested: 'Not requested in the latest selected run',
  }[state];
}

export function readJobId(search: string): string | null {
  const params = new URLSearchParams(search);
  if (!params.has('job')) return null;
  const id = params.get('job') ?? '';
  return id.length > 0 &&
    !/[^0-9]/.test(id) &&
    params.getAll('job').length === 1
    ? id
    : 'invalid';
}
export function changeTimeline<T>(
  items: readonly T[],
  identity: (item: T) => string,
): T[] {
  const result: T[] = [];
  let previous: string | undefined;
  for (const item of items) {
    const key = identity(item);
    if (result.length && key === previous) result[result.length - 1] = item;
    else result.push(item);
    previous = key;
  }
  return result;
}

export function uniqueSkillMentions<T extends { name: string }>(
  skills: T[],
): T[] {
  return [...new Map(skills.map((skill) => [skill.name, skill])).values()];
}
export function jobUrl(href: string, id: string | null): string {
  const url = new URL(href);
  url.searchParams.delete('job');
  if (id !== null) url.searchParams.set('job', id);
  return url.pathname + url.search + url.hash;
}
export function shouldOpenJob(event: {
  button: number;
  metaKey: boolean;
  ctrlKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
  defaultPrevented: boolean;
}): boolean {
  return (
    event.button === 0 &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.altKey &&
    !event.shiftKey &&
    !event.defaultPrevented
  );
}

const groups = [
  [
    'Overview',
    [
      'Job Title:',
      'Job Type:',
      'Number of Job Openings:',
      'Level:',
      'Targeted Degrees and Disciplines:',
    ],
  ],
  [
    'Description and requirements',
    [
      'Job Summary:',
      'Job Responsibilities:',
      'Required Skills:',
      'Special Job Requirements:',
    ],
  ],
  [
    'Work term, location and compensation',
    [
      'Work Term:',
      'Work Term Duration:',
      'Special Work Term Start/End Date Considerations:',
      'Region:',
      'Job - Address Line One:',
      'Job - Address Line Two:',
      'Job - City:',
      'Job - Province/State:',
      'Job - Postal/Zip Code:',
      'Job - Country:',
      'Job Location (If Exact Address Unknown or Multiple Locations):',
      'Employment Location Arrangement:',
      'Additional Employment Arrangement Location Information:',
      'Compensation and Benefits:',
      'Transportation and Housing:',
    ],
  ],
  [
    'Application information',
    [
      'Application Deadline',
      'Application Documents Required:',
      'Application Method:',
      'Additional Application Information:',
    ],
  ],
  [
    'Organization',
    ['Organization:', 'Division:', 'Employer Internal Job Number:'],
  ],
  [
    'Additional information',
    ['Additional Information:', 'Additional Job Identifiers:'],
  ],
] as const;
const knownLabels = new Set<string>(
  groups.flatMap(([, labels]) => [...labels]),
);
function displayContentField(label: string, text: string): string {
  if (label !== 'Targeted Degrees and Disciplines:') return text;
  // Older snapshots saved the heading and popup control, not their values.
  const values = text
    .split('\n')
    .filter((line) => {
      const normalized = line.trim().replace(/\s+/g, ' ');
      return (
        normalized !== 'Targeted Clusters' &&
        normalized !== 'View Targeted Degrees and Disciplines'
      );
    })
    .join('\n');
  return values.trim() ? values : 'Not captured in saved content';
}

export function groupContent(fields: Record<string, string>) {
  return groups
    .map(([title, labels]) => ({
      title,
      fields: labels
        .filter((label) => Object.hasOwn(fields, label))
        .map(
          (label) =>
            [label, displayContentField(label, fields[label])] as const,
        ),
    }))
    .filter((group) => group.fields.length > 0);
}
export function rateLabel(rate: {
  bound_type: string;
  minimum: string | null;
  maximum: string | null;
  minimum_inclusive: boolean | null;
  maximum_inclusive: boolean | null;
  currency?: string | null;
  period?: string | null;
}): string {
  const unit = [rate.currency, rate.period ? `per ${rate.period}` : null]
    .filter(Boolean)
    .join(' ');
  const value = (amount: string | null) =>
    amount === null
      ? 'open'
      : `${rate.currency ? '' : '$'}${groupNumber(amount)}`;
  const amount =
    rate.bound_type === 'fixed'
      ? value(rate.minimum)
      : rate.bound_type === 'range'
        ? `${value(rate.minimum)}–${value(rate.maximum)}`
        : rate.bound_type === 'minimum'
          ? `${rate.minimum_inclusive ? 'At least' : 'More than'} ${value(rate.minimum)}`
          : `${rate.maximum_inclusive ? 'Up to' : 'Less than'} ${value(rate.maximum)}`;
  return `${amount} ${unit}`.trim();
}

export function contentCards(fields: Record<string, string>) {
  const grouped = groupContent(fields);
  const narrative =
    grouped.find((group) => group.title === 'Description and requirements')
      ?.fields ?? [];
  const rest = grouped
    .filter((group) => group.title !== 'Description and requirements')
    .flatMap((group) => group.fields);
  const categories = [
    [
      'Position details',
      [
        'Job Title:',
        'Job Type:',
        'Number of Job Openings:',
        'Level:',
        'Targeted Degrees and Disciplines:',
      ],
    ],
    [
      'Work term',
      [
        'Work Term:',
        'Work Term Duration:',
        'Special Work Term Start/End Date Considerations:',
      ],
    ],
    [
      'Location',
      [
        'Region:',
        'Job - Address Line One:',
        'Job - Address Line Two:',
        'Job - City:',
        'Job - Province/State:',
        'Job - Postal/Zip Code:',
        'Job - Country:',
        'Job Location (If Exact Address Unknown or Multiple Locations):',
        'Employment Location Arrangement:',
        'Additional Employment Arrangement Location Information:',
      ],
    ],
    [
      'Compensation & benefits',
      ['Compensation and Benefits:', 'Transportation and Housing:'],
    ],
    [
      'Application details',
      [
        'Application Deadline',
        'Application Documents Required:',
        'Application Method:',
        'Additional Application Information:',
      ],
    ],
    [
      'Organization',
      ['Organization:', 'Division:', 'Employer Internal Job Number:'],
    ],
    [
      'Additional information',
      ['Additional Information:', 'Additional Job Identifiers:'],
    ],
  ] as const;
  return [
    ...narrative.map((field) => ({
      title: field[0].replace(/:$/, ''),
      fields: [field],
    })),
    ...categories.map(([title, labels]) => ({
      title,
      fields: rest.filter(([label]) =>
        (labels as readonly string[]).includes(label),
      ),
    })),
  ].filter((card) => card.fields.length > 0);
}

export function parseJobDetail(value: unknown, id: string): JobDetail {
  const record = (value: unknown): value is Record<string, unknown> =>
    typeof value === 'object' && value !== null && !Array.isArray(value);
  if (
    !record(value) ||
    value.posting_id !== id ||
    !record(value.listing) ||
    value.listing.id !== id ||
    (value.listing.title != null && typeof value.listing.title !== 'string') ||
    (value.content_observed_at !== null &&
      typeof value.content_observed_at !== 'string')
  )
    throw new Error('Invalid stored posting response.');
  const content = value.content;
  if (content === null) {
    if (value.content_observed_at !== null)
      throw new Error('Invalid stored posting response.');
  } else if (
    !record(content) ||
    content.id !== id ||
    content.coverage !== 'content_only' ||
    !record(content.fields) ||
    typeof value.content_observed_at !== 'string' ||
    !Object.entries(content.fields).every(
      ([label, text]) => knownLabels.has(label) && typeof text === 'string',
    )
  )
    throw new Error('Invalid stored posting response.');
  return value as JobDetail;
}
async function fetchPostingResource<T>(
  id: string,
  resource: 'analysis' | 'history',
  signal: AbortSignal,
): Promise<T> {
  const response = await fetch(
    `/api/postings/${encodeURIComponent(id)}/${resource}`,
    { signal, cache: 'no-store' },
  );
  if (
    !response.ok ||
    !response.headers.get('content-type')?.includes('application/json')
  )
    throw new Error('Stored posting analysis unavailable.');
  const value: unknown = await response.json();
  if (
    typeof value !== 'object' ||
    value === null ||
    !('posting_id' in value) ||
    value.posting_id !== id
  )
    throw new Error('Invalid stored posting analysis response.');
  return value as T;
}

export async function fetchJobDetail(
  id: string,
  signal: AbortSignal,
): Promise<JobDetailView | null> {
  if (!(id.length > 0 && !/[^0-9]/.test(id)))
    throw new Error('Invalid posting ID.');
  const response = await fetch(`/api/postings/${encodeURIComponent(id)}`, {
    signal,
    cache: 'no-store',
  });
  if (response.status === 404) return null;
  if (
    !response.ok ||
    !response.headers.get('content-type')?.includes('application/json')
  )
    throw new Error('Stored posting unavailable.');
  const detail = parseJobDetail(await response.json(), id);
  const [analysis, history] = await Promise.all([
    fetchPostingResource<JobAnalysis>(id, 'analysis', signal),
    fetchPostingResource<JobHistory>(id, 'history', signal),
  ]);
  return { ...detail, analysis, history };
}
