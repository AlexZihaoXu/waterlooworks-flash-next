import { expect, it } from 'vitest';
import {
  compensationForSorting,
  hourlyTier,
  presentSummary,
} from '../../src/lib/hourlyPay';
import { compareCompensation } from '../../src/lib/sorting';

const fixed = {
  bound_type: 'fixed' as const,
  minimum: '8400',
  maximum: null,
  minimum_inclusive: true,
  maximum_inclusive: null,
  currency: 'CAD',
  period: 'month' as const,
  hours_per_week: null,
};
it('defaults currency to CAD before FX and marks display assumptions without changing source', () => {
  const pay: NonNullable<Parameters<typeof presentSummary>[0]> = {
    needs_review: false,
    currency: null,
    period: 'month',
    minimum: '8400',
    maximum: '8400',
    average: '8400',
    estimated: false,
    assumed_hours: false,
    annualized: false,
    hours_per_week: [],
    source_rates: [
      {
        ...fixed,
        currency: null,
        payout_frequency: null,
        eligibility: null,
        source_field: '',
        evidence_text: '',
      },
    ],
  };
  Object.freeze(pay.source_rates[0]);
  Object.freeze(pay);
  const preferences = { format: 'month' as const, currency: 'USD' as const };
  const fx = { as_of: '', cad_per_unit: { CAD: '1', USD: '2' } };
  // Ranking always converts to CAD so currencies interleave by value; the
  // display setting only changes how pay is shown.
  const sorted = compensationForSorting(pay, preferences, fx)!;
  expect(sorted.currency).toBe('CAD');
  expect(sorted.minimum).toBe('8400.' + '0'.repeat(36));
  const displayed = presentSummary(pay, preferences, fx);
  expect(displayed.summary?.currency).toBe('USD');
  expect(displayed.label).toBe('4,200 USD/mo*');
  expect(pay.source_rates[0].currency).toBeNull();
  expect(pay.currency).toBeNull();
  expect(compensationForSorting(pay, preferences, null)?.currency).toBe('CAD');
  const mixed = {
    ...pay,
    source_rates: [
      pay.source_rates[0],
      { ...pay.source_rates[0], currency: 'USD' },
    ],
  };
  expect(
    compareCompensation(compensationForSorting(mixed), null, 'average'),
  ).toBeGreaterThan(0);
});
it('converts monthly tiers using the approved default hours', () => {
  expect(hourlyTier(fixed).label).toBe('48.46 CAD/hr');
  expect(hourlyTier(fixed).note).toContain('40h/week assumed');
  expect(hourlyTier(fixed).note).toContain('52 weeks/year');
});
it('honors stated hours and preserves ranges', () => {
  const result = hourlyTier({
    ...fixed,
    bound_type: 'range',
    minimum: '800',
    maximum: '960',
    period: 'week',
    hours_per_week: '32',
  });
  expect(result.label).toBe('25 - 30 CAD/hr');
  expect(result.note).toBe('32h/week stated');
});
it('retains open-bound meaning and does not assume daily working hours', () => {
  expect(
    hourlyTier({
      ...fixed,
      bound_type: 'minimum',
      minimum: '800',
      period: 'week',
      minimum_inclusive: false,
    }).label,
  ).toBe('> 20 CAD/hr');
  expect(hourlyTier({ ...fixed, minimum: '200', period: 'day' }).label).toBe(
    '200 CAD/day',
  );
});

it('ranks USD pay among CAD pay by converted value, whatever the display currency', () => {
  const rate = (minimum: string, maximum: string, currency: string) => ({
    ...fixed,
    bound_type: 'range' as const,
    period: 'hour' as const,
    minimum,
    maximum,
    currency,
    payout_frequency: null,
    eligibility: null,
    source_field: '',
    evidence_text: '',
  });
  const posting = (minimum: string, maximum: string, currency: string) => ({
    minimum,
    maximum,
    average: null,
    currency,
    period: 'hour' as const,
    needs_review: false,
    estimated: false,
    assumed_hours: false,
    annualized: false,
    hours_per_week: [],
    source_rates: [rate(minimum, maximum, currency)],
  });
  const fx = { as_of: '', cad_per_unit: { CAD: '1', USD: '1.4' } };
  const usd = posting('55', '70', 'USD');
  const cad = posting('40', '50', 'CAD');
  for (const currency of ['original', 'CAD', 'USD'] as const) {
    const preferences = { format: 'hour' as const, currency };
    expect(
      compareCompensation(
        compensationForSorting(usd, preferences, fx),
        compensationForSorting(cad, preferences, fx),
        'maximum',
      ),
    ).toBeLessThan(0);
  }
});
