import { expect, it } from 'vitest';
import { extractionStatusLabel } from '../../src/lib/jobDetail';
import {
  compensationForSorting,
  presentSummary,
} from '../../src/lib/hourlyPay';
import { compareCompensation } from '../../src/lib/sorting';

it('distinguishes accepted empty extraction from unavailable or pending work', () => {
  expect(extractionStatusLabel('accepted')).toBe('Accepted saved extraction');
  expect(extractionStatusLabel('empty')).toBe(
    'Accepted extraction; no usable output',
  );
  expect(extractionStatusLabel('partial')).toMatch(/Partial/);
  expect(extractionStatusLabel('rejected')).toMatch(/Rejected/);
  expect(extractionStatusLabel('stale')).toMatch(/changed/);
  expect(extractionStatusLabel('pending')).toMatch(/Pending/);
  expect(extractionStatusLabel('not_requested')).toMatch(/Not requested/);
});

it('does not rank disputed compensation as missing zero or a confident aggregate', () => {
  const pay = {
    needs_review: true,
    source_rates: [],
    minimum: null,
    maximum: null,
    average: null,
    currency: null,
    period: null,
    estimated: false,
    assumed_hours: false,
    annualized: false,
    hours_per_week: [],
  };
  expect(presentSummary(pay).label).toBe('Needs pay review');
  const sortable = compensationForSorting(pay);
  expect(compareCompensation(sortable, null, 'average')).toBeGreaterThan(0);
});
