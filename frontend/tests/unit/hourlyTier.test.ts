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
  const sorted = compensationForSorting(pay, preferences, fx)!;
  expect(sorted.currency).toBe('USD');
  expect(sorted.minimum).toBe('4200.' + '0'.repeat(36));
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
