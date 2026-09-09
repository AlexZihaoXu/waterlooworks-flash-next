import { expect, it } from 'vitest';
import { parsePayPreferences } from '../../src/lib/usePayPreferences';
import { scalePay } from '../../src/lib/hourlyPay';

it('validates saved display preferences independently of posting data', () => {
  expect(parsePayPreferences(null)).toEqual({
    format: 'hour',
    currency: 'original',
  });
  expect(parsePayPreferences({ format: 'month', currency: 'USD' })).toEqual({
    format: 'month',
    currency: 'USD',
  });
  expect(
    parsePayPreferences({ format: 'invalid', currency: 'invalid' }),
  ).toEqual({ format: 'hour', currency: 'original' });
});
it('scales decimal quantities without floating-point rounding', () => {
  expect(scalePay('100', ['2'], ['4'])).toBe('50.' + '0'.repeat(36));
  expect(scalePay('9007199254740993', ['2'], ['2'])).toBe(
    '9007199254740993.' + '0'.repeat(36),
  );
  expect(scalePay(null, ['2'], [])).toBeNull();
  expect(() => scalePay('100', [], ['0'])).toThrow('Invalid pay divisor');
});
