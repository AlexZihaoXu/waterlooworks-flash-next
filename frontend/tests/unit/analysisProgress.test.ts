import { expect, it } from 'vitest';
import { refreshProgress } from '../../src/lib/refreshProgress';

it('uses AI extraction totals rather than completed listing pages', () => {
  const result = refreshProgress('analysis', [25, 25], [100, 100], [3, 10]);
  expect(result.value).toBe(3);
  expect(result.maxValue).toBe(10);
  expect(result.label).toBe('AI extraction progress');
  expect(result.stageLabel).toBe('Extracting with AI');
});
it('normalization is indeterminate rather than falsely complete', () => {
  expect(
    refreshProgress('normalizing', [25, 25], [100, 100]).isIndeterminate,
  ).toBe(true);
});
