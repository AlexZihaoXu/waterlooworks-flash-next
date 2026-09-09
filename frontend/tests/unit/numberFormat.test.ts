import { expect, it } from 'vitest';
import { groupNumber } from '../../src/lib/numberFormat';

it('groups integer and fractional amounts without losing precision', () => {
  expect(groupNumber('1000')).toBe('1,000');
  expect(groupNumber('1234567')).toBe('1,234,567');
  expect(groupNumber('1234567.80')).toBe('1,234,567.80');
  expect(groupNumber('9007199254740993')).toBe('9,007,199,254,740,993');
  expect(groupNumber('-1234.5')).toBe('-1,234.5');
  expect(groupNumber('--/--')).toBe('--/--');
});
