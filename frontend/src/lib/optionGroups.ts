export type OptionRow =
  | {
      kind: 'heading';
      key: string;
      label: string;
      count: number;
      values: readonly string[];
      depth: number;
      matches: boolean;
      group: string;
    }
  | {
      kind: 'option';
      key: string;
      value: string;
      group: string;
      depth: number;
    };

const faculties: Record<string, string> = {
  ENG: 'Engineering',
  MATH: 'Mathematics',
  SCI: 'Science',
  ARTS: 'Arts',
  ENV: 'Environment',
  HEALTH: 'Health',
};

function categoryPath(value: string, targeted: boolean): string[] {
  if (!targeted) {
    const initial = value.trim().charAt(0).toUpperCase();
    return [/^[A-Z]$/.test(initial) ? initial : 'Others'];
  }
  if (value.startsWith('- Theme - ')) {
    const rest = value.slice('- Theme - '.length);
    const colon = rest.indexOf(':');
    return colon > 0 ? ['Themes', rest.slice(0, colon).trim()] : ['Themes'];
  }
  const prefix = value.split(' - ', 1)[0]!;
  const codes = prefix.split('/');
  if (
    value.includes(' - ') &&
    codes.every((code) => Object.hasOwn(faculties, code))
  )
    return [codes.map((code) => faculties[code]).join(' / ')];
  return ['Others'];
}

export function categorySelection(
  values: readonly string[],
  draft: readonly string[],
) {
  const selected = new Set(draft);
  const count = values.filter((value) => selected.has(value)).length;
  return {
    selected: values.length > 0 && count === values.length,
    indeterminate: count > 0 && count < values.length,
  };
}

export function toggleCategory(
  draft: readonly string[],
  values: readonly string[],
): string[] {
  if (categorySelection(values, draft).selected) {
    const descendants = new Set(values);
    return draft.filter((value) => !descendants.has(value));
  }
  return [...new Set([...draft, ...values])];
}

export function groupedOptionRows(
  options: readonly string[],
  key = '',
  query = '',
): OptionRow[] {
  const term = query.trim().toLocaleLowerCase();
  const leaves = [...new Set(options)]
    .map((value) => ({
      value,
      path: categoryPath(value, key === 'targeted_disciplines'),
    }))
    .filter(({ value, path }) =>
      [value, ...path].some((text) => text.toLocaleLowerCase().includes(term)),
    );
  const rows: OptionRow[] = [];
  const compare = (a: string, b: string) =>
    a === b
      ? 0
      : a === 'Others'
        ? 1
        : b === 'Others'
          ? -1
          : a.localeCompare(b, 'en', { sensitivity: 'base' });
  const visit = (items: typeof leaves, depth: number, parent: string[]) => {
    const labels = [
      ...new Set(items.flatMap((item) => item.path[depth] ?? [])),
    ].sort(compare);
    for (const label of labels) {
      const children = items.filter((item) => item.path[depth] === label);
      const path = [...parent, label];
      const group = path.join(' > ');
      rows.push({
        kind: 'heading',
        key: `heading:${JSON.stringify(path)}`,
        label,
        group,
        count: children.length,
        values: children.map((item) => item.value),
        depth,
        matches: !!term,
      });
      visit(children, depth + 1, path);
    }
    for (const item of items
      .filter((item) => item.path.length === depth)
      .sort((a, b) => compare(a.value, b.value)))
      rows.push({
        kind: 'option',
        key: `option:${item.value}`,
        value: item.value,
        group: parent.join(' > '),
        depth,
      });
  };
  visit(leaves, 0, []);
  return rows;
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
