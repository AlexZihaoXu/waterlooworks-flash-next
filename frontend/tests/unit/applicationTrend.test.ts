import { expect, it } from 'vitest';
import {
  trendPoints,
  trendWindow,
  trendFrame,
  nearestTrendPoint,
} from '../../src/lib/applicationTrend';

it('picks the nearest actual point, with no interpolated values', () => {
  expect(nearestTrendPoint([], 1, 1)).toBe(-1);
  expect(
    nearestTrendPoint(
      [
        [0, 0],
        [10, 20],
      ],
      9,
      19,
    ),
  ).toBe(1);
  expect(
    nearestTrendPoint(
      [
        [0, 0],
        [10, 20],
      ],
      1,
      2,
    ),
  ).toBe(0);
});

it('centers the plotting area with equal gutters, including wide labels', () => {
  for (const length of [1, 5, 21]) {
    const frame = trendFrame(length);
    expect(frame.left).toBe(frame.width - frame.right);
    expect(frame.right - frame.left).toBeGreaterThanOrEqual(80);
  }
});

it('keeps unknown counts as gaps and sorts timestamps without inventing counts', () => {
  expect(
    trendPoints([
      [2000, 5],
      [1000, null],
      [3000, -1],
      [NaN, 3],
    ]),
  ).toEqual([
    { time: 1000, count: null },
    { time: 2000, count: 5 },
    { time: 3000, count: null },
  ]);
});
it('reports exact 24-hour net changes including decreases', () => {
  expect(
    trendWindow(
      trendPoints([
        [0, 10],
        [86400000, 8],
      ]),
    ),
  ).toEqual({ delta: -2, hours: 24 });
});
it('does not extrapolate a sparse baseline into a 24-hour change', () => {
  expect(
    trendWindow(
      trendPoints([
        [0, 5],
        [90000000, 9],
      ]),
    ),
  ).toBeNull();
  expect(
    trendWindow(
      trendPoints([
        [0, 5],
        [86400000, 8],
        [90000000, 9],
      ]),
    ),
  ).toEqual({ delta: 1, hours: 1 });
});
it('requires two distinct instants with known endpoints', () => {
  expect(trendWindow([])).toBeNull();
  expect(
    trendWindow(
      trendPoints([
        [0, 5],
        [0, 8],
      ]),
    ),
  ).toBeNull();
  expect(
    trendWindow(
      trendPoints([
        [0, 5],
        [3600000, null],
      ]),
    ),
  ).toBeNull();
  expect(
    trendWindow(
      trendPoints([
        [0, null],
        [3600000, 8],
      ]),
    ),
  ).toBeNull();
});
