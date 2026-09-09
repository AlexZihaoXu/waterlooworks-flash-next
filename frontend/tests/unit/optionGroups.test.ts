import { describe, expect, it } from 'vitest';
import { groupedOptionRows, visibleRows } from '../../src/lib/optionGroups';

// Generic alphabetic grouping and viewport arithmetic, not posting fixtures.
describe('grouped option list', () => {
  it('orders A–Z headings, counts their options, and puts Others last', () => {
    const rows = groupedOptionRows([
      'beta',
      'alpha',
      'Alpine',
      '9value',
      '?value',
    ]);
    expect(
      rows
        .filter((row) => row.kind === 'heading')
        .map(({ label, count }) => ({
          kind: 'heading',
          key: `heading:${label}`,
          label,
          count,
        })),
    ).toEqual([
      { kind: 'heading', key: 'heading:A', label: 'A', count: 2 },
      { kind: 'heading', key: 'heading:B', label: 'B', count: 1 },
      { kind: 'heading', key: 'heading:Others', label: 'Others', count: 2 },
    ]);
    expect(
      rows.filter((row) => row.kind === 'option').map((row) => row.value),
    ).toEqual(['alpha', 'Alpine', 'beta', '?value', '9value']);
    expect(groupedOptionRows([])).toEqual([]);
  });
  it('uses compact rows and reuses its buffer for nearby scrolling', () => {
    expect(visibleRows(10000, 400, 0)).toEqual(visibleRows(10000, 400, 360));
    const middle = visibleRows(10000, 400, 800);
    expect(middle.top).toBe(middle.start * 40);
    expect(middle.bottom + middle.top + (middle.end - middle.start) * 40).toBe(
      400000,
    );
    expect(middle.start).toBeLessThanOrEqual(20);
    expect(middle.end).toBeGreaterThanOrEqual(30);
    expect(middle.end - middle.start).toBeLessThanOrEqual(41);
  });
  it('keeps the mounted list bounded and reaches its final item', () => {
    expect(visibleRows(10000, 400, 0)).toMatchObject({ start: 0, top: 0 });
    expect(visibleRows(10000, 400, 0).end).toBeLessThanOrEqual(41);
    expect(visibleRows(10000, 400, 560000)).toMatchObject({
      end: 10000,
      bottom: 0,
    });
    expect(visibleRows(0, 0, 0)).toMatchObject({
      start: 0,
      end: 0,
      top: 0,
      bottom: 0,
    });
  });
});
