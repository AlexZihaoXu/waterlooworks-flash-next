export type OptionRow =
  | { kind: 'heading'; key: string; label: string; count: number }
  | { kind: 'option'; key: string; value: string; group: string };

export function groupedOptionRows(options: readonly string[]): OptionRow[] {
  const groups = new Map<string, string[]>();
  for (const value of options) {
    const initial = value.trim().charAt(0).toUpperCase();
    const group = /^[A-Z]$/.test(initial) ? initial : 'Others';
    const values = groups.get(group) ?? [];
    values.push(value);
    groups.set(group, values);
  }
  const labels = [...groups.keys()].sort((a, b) =>
    a === 'Others' ? 1 : b === 'Others' ? -1 : a.localeCompare(b, 'en'),
  );
  return labels.flatMap((label) => {
    const values = groups
      .get(label)!
      .sort((a, b) => a.localeCompare(b, 'en', { sensitivity: 'base' }));
    return [
      { kind: 'heading', key: `heading:${label}`, label, count: values.length },
      ...values.map((value) => ({
        kind: 'option' as const,
        key: `option:${value}`,
        value,
        group: label,
      })),
    ] as OptionRow[];
  });
}

export const OPTION_ROW_HEIGHT = 40;

// Recycle in viewport-sized blocks, not on every pixel/row of scrolling.
// Keep one block behind and two ahead to cover wheel, touch and keyboard bursts.
export function visibleRows(count: number, height: number, scrollTop: number) {
  const block = Math.max(4, Math.ceil(height / OPTION_ROW_HEIGHT));
  const offset = Math.max(
    0,
    Math.min(scrollTop, count * OPTION_ROW_HEIGHT - height),
  );
  const first = Math.floor(offset / OPTION_ROW_HEIGHT / block) * block;
  const start = Math.max(0, first - block);
  const end = Math.min(count, first + 3 * block + 1);
  return {
    start,
    end,
    top: start * OPTION_ROW_HEIGHT,
    bottom: (count - end) * OPTION_ROW_HEIGHT,
  };
}
