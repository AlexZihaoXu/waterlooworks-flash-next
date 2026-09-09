import { expect, it } from 'vitest';
import { compactPay, compensationNotes } from '../../src/lib/sorting';

it('discloses reduced hours and conversion assumptions', () => {
  const pay = {
    minimum: '25',
    maximum: '25',
    average: '25',
    currency: 'CAD',
    period: 'hour' as const,
    estimated: true,
    assumed_hours: false,
    annualized: false,
    hours_per_week: ['32'],
  };
  expect(compensationNotes(pay)).toBe('32h/week stated');
  expect(
    compensationNotes({
      ...pay,
      hours_per_week: [],
      assumed_hours: true,
      annualized: true,
    }),
  ).toBe('40h/week assumed · 52 weeks/year');
});

it('formats fixed and ranged pay using native units', () => {
  expect(
    compactPay({
      minimum: '123.40',
      maximum: '567.80',
      currency: 'CAD',
      period: 'hour',
    }),
  ).toBe('123.4 - 567.8 CAD/hr');
  expect(
    compactPay({
      minimum: '25.00',
      maximum: '25.00',
      currency: 'USD',
      period: 'hour',
    }),
  ).toBe('25 USD/hr');
  expect(
    compactPay({
      minimum: '250',
      maximum: '250',
      currency: 'CAD',
      period: 'day',
    }),
  ).toBe('250 CAD/day');
});
it('shows missing pay explicitly without inventing bounds', () => {
  expect(compactPay(null)).toBe('--/--');
  expect(
    compactPay({ minimum: null, maximum: null, currency: null, period: null }),
  ).toBe('--/--');
  expect(
    compactPay({
      minimum: '25',
      maximum: null,
      currency: 'CAD',
      period: 'hour',
    }),
  ).toBe('25 CAD/hr (lower bound)');
});
it('rounds derived amounts without floating-point loss', () => {
  expect(
    compactPay({
      minimum: '9007199254740993.555',
      maximum: '9007199254740993.555',
      currency: 'CAD',
      period: 'hour',
    }),
  ).toBe('9,007,199,254,740,993.56 CAD/hr');
});
