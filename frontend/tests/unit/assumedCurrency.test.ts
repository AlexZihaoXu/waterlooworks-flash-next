import { expect, it } from 'vitest';
import { presentRate, presentSummary } from '../../src/lib/hourlyPay';
import type { components } from '../../src/lib/api.generated';

const rate: components['schemas']['CompensationRate'] = {
  bound_type: 'fixed',
  minimum: '40',
  maximum: '40',
  minimum_inclusive: true,
  maximum_inclusive: true,
  currency: null,
  period: 'hour',
  hours_per_week: null,
  payout_frequency: null,
  eligibility: null,
  source_field: '',
  evidence_text: '',
};
const pay: components['schemas']['CompensationSort'] = {
  needs_review: false,
  estimated: false,
  assumed_hours: false,
  annualized: false,
  hours_per_week: [],
  source_rates: [rate],
  currency: null,
  period: 'hour',
  minimum: '40',
  maximum: '40',
  average: '40',
};
it('marks assumed CAD on detail and aggregate labels without changing sources', () => {
  expect(presentRate(rate).label).toBe('40 CAD/hr*');
  expect(presentSummary(pay).label).toBe('40 CAD/hr*');
  expect(rate.currency).toBeNull();
  expect(pay.currency).toBeNull();
  expect(presentRate({ ...rate, currency: 'CAD' }).label).toBe('40 CAD/hr');
  expect(presentSummary(null).label).toBe('--/--');
});
it('preserves the assumption marker through conversions without inventing periods', () => {
  expect(
    presentRate(rate, { format: 'month', currency: 'original' }).label,
  ).toBe('6,933.33 CAD/mo*');
  expect(presentRate({ ...rate, period: null }).label).toBe('40 CAD*');
  const fx = { as_of: '2026-01-01', cad_per_unit: { CAD: '1', USD: '1.25' } };
  expect(presentRate(rate, { format: 'hour', currency: 'USD' }, fx).label).toBe(
    '32 USD/hr*',
  );
  expect(
    presentSummary(pay, { format: 'hour', currency: 'USD' }, fx).label,
  ).toBe('32 USD/hr*');
});
