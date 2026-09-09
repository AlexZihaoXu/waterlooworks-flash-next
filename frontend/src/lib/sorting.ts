import type { Posting } from './workspace';
import { groupNumber } from './numberFormat';
import { compareRemaining, parseDeadline } from './deadlines';

export const sortLabels = {
  id: 'Default (posting ID)',
  newest: 'Recently added',
  remaining: 'Time remaining',
  'applications-desc': 'Most applications',
  'openings-desc': 'Most openings',
  organization: 'Organization',
  'compensation-average': 'Compensation: Average',
  'compensation-minimum': 'Compensation: Minimum',
  'compensation-maximum': 'Compensation: Maximum',
} as const;
export type PostingSort = keyof typeof sortLabels;
type Kind = 'text' | 'count' | 'date';
export type CompensationMetric = 'minimum' | 'maximum' | 'average';
type Pay =
  | (Pick<
      NonNullable<Posting['compensation_sort']>,
      'minimum' | 'maximum' | 'average' | 'currency' | 'period'
    > &
      Partial<
        Pick<
          NonNullable<Posting['compensation_sort']>,
          'source_rates' | 'needs_review'
        >
      >)
  | null;

function compactAmount(value: string): string {
  const [whole, fraction = ''] = value.split('.');
  const digits = fraction.padEnd(3, '0');
  const cents =
    BigInt(whole) * 100n +
    BigInt(digits.slice(0, 2)) +
    (digits[2] >= '5' ? 1n : 0n);
  if (cents === 0n && /[1-9]/.test(value)) return '<0.01';
  const decimals = (cents % 100n)
    .toString()
    .padStart(2, '0')
    .replace(/0+$/, '');
  return groupNumber(`${cents / 100n}${decimals ? '.' + decimals : ''}`);
}
export function compactPay(
  pay: Omit<NonNullable<Pay>, 'average'> | null,
): string {
  if (!pay || (pay.minimum === null && pay.maximum === null)) return '--/--';
  const abbreviations = {
    hour: 'hr',
    day: 'day',
    week: 'wk',
    month: 'mo',
    year: 'yr',
  };
  const unit = `${pay.currency ?? ''}${pay.period ? '/' + abbreviations[pay.period] : ''}`;
  const lo = pay.minimum === null ? null : compactAmount(pay.minimum);
  const hi = pay.maximum === null ? null : compactAmount(pay.maximum);
  const amount =
    lo !== null && hi !== null
      ? pay.minimum === pay.maximum
        ? lo
        : `${lo} - ${hi}`
      : (lo ?? hi);
  const qualifier =
    lo === null ? ' (upper bound)' : hi === null ? ' (lower bound)' : '';
  return `${amount}${unit ? (pay.currency ? ' ' : '') + unit : ''}${qualifier}`;
}
export function compensationNotes(
  pay: Omit<
    NonNullable<Posting['compensation_sort']>,
    'source_rates' | 'needs_review'
  > | null,
): string {
  if (!pay) return '';
  const notes: string[] = [];
  if (!pay.currency) notes.push('unspecified');
  if (!pay.period) notes.push('Pay period unspecified');
  for (const hours of pay.hours_per_week ?? []) {
    if (Number(hours) < 40 || pay.estimated)
      notes.push(`${hours}h/week stated`);
  }
  if (pay.assumed_hours) notes.push('40h/week assumed');
  if (pay.annualized) notes.push('52 weeks/year');
  return notes.join(' · ');
}

export function compensationMetric(
  sort: PostingSort,
): CompensationMetric | null {
  return sort.startsWith('compensation-')
    ? (sort.slice(13) as CompensationMetric)
    : null;
}
export function compareCompensation(
  left: Pay,
  right: Pay,
  metric: CompensationMetric,
): number {
  // Ranking-only defaults. Incompatible rates and missing open-bound endpoints
  // remain unknown; neither is evidence that the posting has no salary.
  const value = (pay: Pay) => {
    // Daily amounts have no defensible hourly/monthly conversion without
    // hours per day. Keep them in the unrankable tail, not an alphabetic cohort.
    if (pay?.needs_review || pay?.period === 'day') return null;
    if (
      !pay ||
      (pay.minimum === null &&
        pay.maximum === null &&
        !pay.source_rates?.length)
    )
      return '0';
    return pay.period ? pay[metric] : null;
  };
  const a = value(left),
    b = value(right);
  if (a == null) return b == null ? 0 : 1;
  if (b == null) return -1;
  // Zero belongs below every positive currency cohort, not ahead of USD just
  // because the fallback currency is CAD. Explicit zero and missing pay tie.
  const aZero = /^0+(?:\.0+)?$/.test(a),
    bZero = /^0+(?:\.0+)?$/.test(b);
  if (aZero || bZero) return aZero ? (bZero ? 0 : 1) : -1;
  const cohort =
    (left?.currency ?? 'CAD').localeCompare(right?.currency ?? 'CAD') ||
    (left?.period ?? '').localeCompare(right?.period ?? '');
  if (cohort) return cohort;
  const [ai, af = ''] = a.split('.');
  const [bi, bf = ''] = b.split('.');
  const ax = ai.replace(/^0+(?=\d)/, '');
  const bx = bi.replace(/^0+(?=\d)/, '');
  const length = Math.max(af.length, bf.length);
  const compare = (x: string, y: string) => (x < y ? -1 : x > y ? 1 : 0);
  return (
    -(
      ax.length - bx.length ||
      compare(ax, bx) ||
      compare(af.padEnd(length, '0'), bf.padEnd(length, '0'))
    ) || 0
  );
}

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
      case 'compensation-average':
      case 'compensation-minimum':
      case 'compensation-maximum':
        comparison = compareCompensation(
          a.compensation_sort,
          b.compensation_sort,
          compensationMetric(sort)!,
        );
        break;
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
