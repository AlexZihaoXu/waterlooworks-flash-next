import { expect, it } from 'vitest';
import { rateLabel } from '../../src/lib/jobDetail';

const fixed = {
  bound_type: 'fixed',
  minimum: '25.00',
  maximum: null,
  minimum_inclusive: true,
  maximum_inclusive: null,
  period: 'hour',
};
it('includes an explicitly identified currency without changing decimal precision', () => {
  expect(rateLabel({ ...fixed, currency: 'CAD' })).toBe('25.00 CAD per hour');
  expect(rateLabel({ ...fixed, currency: 'USD' })).toBe('25.00 USD per hour');
});
it('uses a generic dollar sign for an unspecified currency', () => {
  expect(rateLabel(fixed)).toBe('$25.00 per hour');
  expect(rateLabel({ ...fixed, currency: null })).toBe('$25.00 per hour');
});
it('preserves range and open-bound meaning when currency is unknown', () => {
  expect(rateLabel({ ...fixed, bound_type: 'range', maximum: '30.50' })).toBe(
    '$25.00–$30.50 per hour',
  );
  expect(rateLabel({ ...fixed, bound_type: 'minimum' })).toBe(
    'At least $25.00 per hour',
  );
  expect(
    rateLabel({
      ...fixed,
      bound_type: 'maximum',
      maximum: '30.50',
      maximum_inclusive: false,
    }),
  ).toBe('Less than $30.50 per hour');
});
