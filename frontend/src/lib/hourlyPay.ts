import type { components } from './api.generated';
import { compactPay, compensationNotes } from './sorting';

type Rate = Pick<
  components['schemas']['CompensationRate'],
  | 'bound_type'
  | 'minimum'
  | 'maximum'
  | 'minimum_inclusive'
  | 'maximum_inclusive'
  | 'currency'
  | 'period'
  | 'hours_per_week'
>;
export type PayPreferences = {
  format: 'hour' | 'month';
  currency: 'original' | 'CAD' | 'USD';
};
export type FxRates = { as_of: string; cad_per_unit: Record<string, string> };
export const defaultPayPreferences: PayPreferences = {
  format: 'hour',
  currency: 'original',
};

function fraction(value: string): [bigint, bigint] {
  const [whole, decimals = ''] = value.split('.');
  return [BigInt(whole + decimals), 10n ** BigInt(decimals.length)];
}
/** Positive decimal multiplication/division, retaining 36 fractional digits for display rounding. */
export function scalePay(
  value: string | null,
  multiply: string[],
  divide: string[],
): string | null {
  if (value === null) return null;
  let [numerator, denominator] = fraction(value);
  for (const part of multiply) {
    const [n, d] = fraction(part);
    numerator *= n;
    denominator *= d;
  }
  for (const part of divide) {
    const [n, d] = fraction(part);
    if (n <= 0n) throw new Error('Invalid pay divisor');
    numerator *= d;
    denominator *= n;
  }
  const scale = 10n ** 36n;
  const scaled = (numerator * scale) / denominator;
  return `${scaled / scale}.${(scaled % scale).toString().padStart(36, '0')}`;
}

export function presentRate(
  rate: Rate,
  preferences = defaultPayPreferences,
  fx: FxRates | null = null,
) {
  let period = rate.period;
  const assumedCurrency = rate.currency === null;
  let currency = rate.currency ?? 'CAD';
  const multiply: string[] = [],
    divide: string[] = [];
  const hours = rate.hours_per_week ?? '40';
  const convertible =
    period !== null && ['hour', 'week', 'month', 'year'].includes(period);
  const convertedPeriod = convertible && period !== preferences.format;
  if (convertedPeriod) {
    if (period === 'week') divide.push(hours);
    if (period === 'month') {
      multiply.push('12');
      divide.push('52', hours);
    }
    if (period === 'year') divide.push('52', hours);
    if (preferences.format === 'month') {
      multiply.push(hours, '52');
      divide.push('12');
    }
    period = preferences.format;
  }
  let fxNote = '';
  if (
    preferences.currency !== 'original' &&
    currency &&
    preferences.currency !== currency
  ) {
    const from = fx?.cad_per_unit[currency];
    const to = fx?.cad_per_unit[preferences.currency];
    if (from && to) {
      multiply.push(from);
      divide.push(to);
      currency = preferences.currency;
      fxNote = `Approx. FX · ${fx!.as_of}`;
    } else fxNote = 'FX unavailable; original currency';
  }
  const minimum = scalePay(rate.minimum, multiply, divide);
  const maximum = scalePay(
    rate.bound_type === 'fixed' ? rate.minimum : rate.maximum,
    multiply,
    divide,
  );
  const summary = {
    currency,
    period,
    minimum,
    maximum,
    average: null,
    estimated: convertedPeriod,
    assumed_hours: convertedPeriod && rate.hours_per_week === null,
    annualized:
      convertedPeriod &&
      (rate.period === 'month' ||
        rate.period === 'year' ||
        preferences.format === 'month'),
    hours_per_week: rate.hours_per_week ? [rate.hours_per_week] : [],
  };
  let label = compactPay(summary);
  if (rate.bound_type === 'minimum')
    label = label
      .replace(' (lower bound)', '')
      .replace(/^/, rate.minimum_inclusive ? '≥ ' : '> ');
  if (rate.bound_type === 'maximum')
    label = label
      .replace(' (upper bound)', '')
      .replace(/^/, rate.maximum_inclusive ? '≤ ' : '< ');
  return {
    label: label + (assumedCurrency && label !== '--/--' ? '*' : ''),
    note: [
      compensationNotes(summary),
      fxNote,
      assumedCurrency ? 'Currency assumed CAD' : '',
    ]
      .filter(Boolean)
      .join(' · '),
    summary,
  };
}
export function hourlyTier(rate: Rate) {
  return presentRate(rate);
}

/** Ranking-only currency default, applied before FX and tier aggregation. */
export function compensationForSorting(
  pay: components['schemas']['CompensationSort'] | null,
  preferences = defaultPayPreferences,
  fx: FxRates | null = null,
) {
  if (!pay) return null;
  if (pay.needs_review)
    return { ...pay, minimum: null, maximum: null, average: null };
  const assumed = {
    ...pay,
    currency: pay.currency ?? 'CAD',
    source_rates: pay.source_rates.map((rate) => ({
      ...rate,
      currency: rate.currency ?? 'CAD',
    })),
  };
  // Rank in CAD whatever the display currency, so USD and CAD pay interleave
  // by value instead of sorting as separate currency groups. Without FX the
  // original currency is kept and the comparator falls back to grouping.
  return (
    presentSummary(assumed, { ...preferences, currency: 'CAD' }, fx)
      .summary ?? {
      ...assumed,
      minimum: null,
      maximum: null,
      average: null,
    }
  );
}

export function presentSummary(
  pay: components['schemas']['CompensationSort'] | null,
  preferences = defaultPayPreferences,
  fx: FxRates | null = null,
) {
  if (!pay) return { label: '--/--', note: '', summary: null };
  if (pay.needs_review)
    return {
      label: 'Needs pay review',
      note: 'Conflicting saved rate evidence; aggregate withheld',
      summary: null,
    };
  if (!pay.source_rates?.length) {
    const summary = { ...pay, currency: pay.currency ?? 'CAD' };
    const label = compactPay(summary);
    return {
      label: label + (pay.currency === null && label !== '--/--' ? '*' : ''),
      note: [
        compensationNotes(pay),
        pay.currency === null ? 'Currency assumed CAD' : '',
      ]
        .filter(Boolean)
        .join(' · '),
      summary,
    };
  }
  const tiers = pay.source_rates.map((rate) =>
    presentRate(rate, preferences, fx),
  );
  const units = new Set(
    tiers.map((t) => `${t.summary.currency}|${t.summary.period}`),
  );
  if (units.size !== 1)
    return {
      label: '--/--',
      note: 'Mixed currencies or pay periods',
      summary: null,
    };
  const compare = (a: string, b: string) => {
    const [an, ad] = fraction(a),
      [bn, bd] = fraction(b);
    const d = an * bd - bn * ad;
    return d < 0n ? -1 : d > 0n ? 1 : 0;
  };
  const lows = tiers.map((t) => t.summary.minimum);
  const highs = tiers.map((t) => t.summary.maximum);
  const minimum = lows.every((v) => v !== null) ? lows.sort(compare)[0] : null;
  const maximum = highs.every((v) => v !== null)
    ? highs.sort(compare).at(-1)!
    : null;
  let average: string | null = null;
  if (minimum !== null && maximum !== null) {
    const [an, ad] = fraction(minimum),
      [bn, bd] = fraction(maximum);
    const scale = 10n ** 36n;
    const n = ((an * bd + bn * ad) * scale) / (2n * ad * bd);
    average = `${n / scale}.${(n % scale).toString().padStart(36, '0')}`;
  }
  const summary = { ...pay, ...tiers[0].summary, minimum, maximum, average };
  const note = [
    ...new Set(tiers.flatMap((t) => t.note.split(' · ')).filter(Boolean)),
  ].join(' · ');
  const label = compactPay(summary);
  return {
    label:
      label +
      (pay.source_rates.some((rate) => rate.currency === null) &&
      label !== '--/--'
        ? '*'
        : ''),
    note,
    summary,
  };
}
