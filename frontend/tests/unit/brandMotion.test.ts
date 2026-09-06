import { describe, expect, it } from 'vitest';
import { settleAngle } from '../../src/lib/brandMotion';

// Generic rotation arithmetic, not source/API fixtures.
describe('brand rotation', () => {
  it('settles along the shortest equivalent angle without a visual jump', () => {
    expect(settleAngle(350)).toBe(-10);
    expect(settleAngle(-350)).toBe(10);
    expect(settleAngle(720)).toBe(0);
    expect(settleAngle(-720)).toBe(0);
    for (const value of [-1085, -361, -180, -10, 0, 20, 180, 361, 1095]) {
      const angle = settleAngle(value);
      expect(Math.abs(angle)).toBeLessThanOrEqual(180);
      expect(Math.abs((value - angle) % 360)).toBe(0);
    }
  });
});
