export type TrendPoint = { time: number; count: number | null };

export function nearestTrendPoint(
  points: readonly (readonly [number, number])[],
  x: number,
  y: number,
) {
  let closest = -1;
  let distance = Infinity;
  points.forEach(([px, py], index) => {
    const candidate = (px - x) ** 2 + (py - y) ** 2;
    if (candidate < distance) {
      closest = index;
      distance = candidate;
    }
  });
  return closest;
}

export function trendFrame(labelLength: number) {
  const left = Math.max(28, Math.min(132, labelLength * 6 + 10));
  const width = Math.max(200, left * 2 + 80);
  return { left, right: width - left, width };
}

// Primitive arithmetic only: no interpolation, count coercion or missing-as-zero.
export function trendPoints(
  values: readonly (readonly [number, number | null])[],
): TrendPoint[] {
  return values
    .filter(
      ([time]) => Number.isFinite(time) && Math.abs(time) <= 8640000000000000,
    )
    .map(([time, count]) => ({
      time,
      count:
        count !== null && Number.isSafeInteger(count) && count >= 0
          ? count
          : null,
    }))
    .sort((a, b) => a.time - b.time);
}

// A window ending at the latest saved observation, not at the current clock.
// Never substitute an older observation for an unobserved 24-hour boundary.
export function trendWindow(points: readonly TrendPoint[]) {
  const latest = points.at(-1);
  if (!latest || latest.count === null) return null;
  const first = points.find(
    (p) => p.time >= latest.time - 86400000 && p.count !== null,
  );
  if (!first || first.count === null || first.time >= latest.time) return null;
  return {
    delta: latest.count - first.count,
    hours: (latest.time - first.time) / 3600000,
  };
}
