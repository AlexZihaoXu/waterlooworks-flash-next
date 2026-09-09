import { expect, it } from 'vitest';
import { salaryTier, type SalaryTierRate } from '../../src/lib/salaryTier';

const rate = (maximum: string, currency = 'CAD'): SalaryTierRate => ({
  bound_type: 'range',
  minimum: '1',
  maximum,
  currency,
  period: 'hour',
  hours_per_week: null,
});
it('uses exact maximum-pay boundaries, not the midpoint', () => {
  for (const [amount, tier] of [
    ['19.99', 'neutral'],
    ['20', 'green'],
    ['29.99', 'green'],
    ['30', 'blue'],
    ['40', 'gold'],
    ['50', 'purple'],
    ['65', 'red'],
    ['80', 'top'],
    ['79.999999999999999999', 'red'],
  ] as const)
    expect(salaryTier([rate(amount)])).toBe(tier);
  expect(salaryTier([rate('20'), rate('80')])).toBe('top');
});
it('normalizes known currencies and periods without display assumptions', () => {
  expect(salaryTier([rate('50', 'USD')])).toBe('neutral');
  expect(
    salaryTier([rate('50', 'USD')], {
      as_of: '2026-01-01',
      cad_per_unit: { USD: '1.4' },
    }),
  ).toBe('red');
  expect(salaryTier([{ ...rate('104000'), period: 'year' }])).toBe('purple');
  expect(
    salaryTier([{ ...rate('3200'), period: 'week', hours_per_week: '40' }]),
  ).toBe('top');
  expect(salaryTier([{ ...rate('13000'), period: 'month' }])).toBe('red');
});
it('defaults unspecified currency to CAD only for styling', () => {
  const unspecified = { ...rate('60'), currency: null };
  expect(salaryTier([unspecified])).toBe('purple');
  expect(unspecified.currency).toBeNull();
});
it('keeps unknown maxima, units and disputes neutral', () => {
  expect(salaryTier([])).toBe('neutral');
  for (const unknown of [
    { ...rate('90'), period: null },
    { ...rate('90'), period: 'day' as const },
    { ...rate('90'), bound_type: 'minimum' as const, maximum: null },
  ])
    expect(salaryTier([rate('80'), unknown])).toBe('neutral');
  expect(salaryTier([rate('90')], null, true)).toBe('neutral');
  expect(
    salaryTier([
      { ...rate('1'), bound_type: 'fixed', minimum: '65', maximum: null },
    ]),
  ).toBe('red');
});
