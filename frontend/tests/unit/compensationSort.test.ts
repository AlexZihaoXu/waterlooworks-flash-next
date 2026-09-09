import { expect, it } from 'vitest';
import { compareCompensation } from '../../src/lib/sorting';

const rate = (
  value: string | null,
  currency: string | null = 'CAD',
  period: 'hour' | 'year' = 'hour',
) => ({ currency, period, minimum: value, maximum: value, average: value });
it('sorts exact decimal amounts descending without floating-point rounding', () => {
  expect(
    compareCompensation(rate('25.01'), rate('25.001'), 'average'),
  ).toBeLessThan(0);
  expect(
    compareCompensation(
      rate('9007199254740993'),
      rate('9007199254740992'),
      'minimum',
    ),
  ).toBeLessThan(0);
  expect(compareCompensation(rate('25.0'), rate('25.00'), 'maximum')).toBe(0);
});
it('places missing pay below positive pay', () => {
  expect(compareCompensation(null, rate('25'), 'average')).toBeGreaterThan(0);
  expect(
    compareCompensation(rate(null), rate('25'), 'average'),
  ).toBeGreaterThan(0);
  expect(compareCompensation(rate(null), null, 'average')).toBe(0);
});
it('treats missing salary as zero across currency cohorts and all metrics', () => {
  for (const metric of ['minimum', 'maximum', 'average'] as const) {
    expect(compareCompensation(null, rate('0.00'), metric)).toBe(0);
    expect(compareCompensation(rate(null), rate('0', 'USD'), metric)).toBe(0);
    expect(compareCompensation(null, rate('1', 'USD'), metric)).toBeGreaterThan(
      0,
    );
    expect(
      compareCompensation(rate('1', 'USD'), rate(null), metric),
    ).toBeLessThan(0);
  }
});
it('assumes CAD only for comparing unspecified currencies', () => {
  const unspecified = Object.freeze(rate('30', null));
  expect(compareCompensation(unspecified, rate('25'), 'average')).toBeLessThan(
    0,
  );
  expect(compareCompensation(unspecified, rate('30'), 'average')).toBe(0);
  expect(unspecified.currency).toBeNull();
});
it('does not turn unknown periods or one-sided endpoints into zero pay', () => {
  expect(
    compareCompensation({ ...rate('25'), period: null }, null, 'average'),
  ).toBeGreaterThan(0);
  expect(
    compareCompensation(
      { ...rate('25'), maximum: null, average: null },
      null,
      'average',
    ),
  ).toBeGreaterThan(0);
});
it('keeps unconvertible daily rates after comparable pay for every metric', () => {
  const daily = { ...rate('24.65'), period: 'day' as const };
  for (const metric of ['minimum', 'maximum', 'average'] as const) {
    expect(compareCompensation(daily, rate('130.95'), metric)).toBeGreaterThan(
      0,
    );
    expect(compareCompensation(rate('1', 'USD'), daily, metric)).toBeLessThan(
      0,
    );
    expect(compareCompensation(daily, null, metric)).toBeGreaterThan(0);
    expect(
      compareCompensation(
        { ...daily, maximum: '999999', minimum: '999999', average: '999999' },
        rate('1'),
        metric,
      ),
    ).toBeGreaterThan(0);
  }
  expect(daily.period).toBe('day');
});
it('groups currencies and periods rather than comparing incompatible numbers', () => {
  expect(
    compareCompensation(rate('1', 'CAD'), rate('999', 'USD'), 'average'),
  ).toBeLessThan(0);
  expect(
    compareCompensation(rate('1'), rate('99999', 'CAD', 'year'), 'average'),
  ).toBeLessThan(0);
});
