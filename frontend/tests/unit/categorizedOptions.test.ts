import { expect, it } from 'vitest';
import {
  groupedOptionRows,
  categorySelection,
  toggleCategory,
} from '../../src/lib/optionGroups';
import { matchesAnySelection } from '../../src/lib/filterSelection';
import { readBrowseUrl, writeBrowseUrl } from '../../src/lib/browseUrl';

// Source-prefix and selection primitives only; no posting fixtures.
it('groups exact targeted leaves without splitting compound or combined names', () => {
  const theme = '- Theme - Computing: Software Development';
  const combined = 'ARTS/ENV/MATH/SCI - Chartered Professional Accounting';
  const rows = groupedOptionRows(
    [theme, combined, 'ENG - Electrical and Computer Engineering', 'unknown'],
    'targeted_disciplines',
  );
  expect(rows.filter((r) => r.kind === 'heading').map((r) => r.label)).toEqual([
    'Arts / Environment / Mathematics / Science',
    'Engineering',
    'Themes',
    'Computing',
    'Others',
  ]);
  expect(
    rows.find((r) => r.kind === 'heading' && r.label === 'Themes'),
  ).toMatchObject({ values: [theme], count: 1 });
  expect(rows.filter((r) => r.kind === 'option').map((r) => r.value)).toContain(
    combined,
  );
});
it('bulk selection is logical, partial selects all, and clear preserves unknown/unrelated selections', () => {
  const values = Array.from({ length: 100 }, (_, i) => `a${i}`);
  expect(categorySelection(values, [])).toEqual({
    selected: false,
    indeterminate: false,
  });
  expect(categorySelection(values, ['a0'])).toEqual({
    selected: false,
    indeterminate: true,
  });
  const all = toggleCategory(['unknown', 'b', 'a0'], values);
  expect(categorySelection(values, all)).toEqual({
    selected: true,
    indeterminate: false,
  });
  expect(toggleCategory(all, values)).toEqual(['unknown', 'b']);
});
it('search includes category names and scopes bulk to matching descendants', () => {
  const values = [
    '- Theme - Computing: Software Development',
    '- Theme - Computing: Other',
    'ENG - Other',
  ];
  const rows = groupedOptionRows(values, 'targeted_disciplines', 'software');
  const themes = rows.find((r) => r.kind === 'heading' && r.label === 'Themes');
  expect(themes).toMatchObject({
    values: [values[0]],
    count: 1,
    matches: true,
  });
  if (themes?.kind === 'heading')
    expect(toggleCategory([values[1]!, 'unknown'], themes.values)).toEqual([
      values[1],
      'unknown',
      values[0],
    ]);
  expect(
    groupedOptionRows(values, 'targeted_disciplines', 'computing').filter(
      (r) => r.kind === 'option',
    ),
  ).toHaveLength(2);
  expect(
    groupedOptionRows(['alpha', 'beta'], 'location', 'a').filter(
      (r) => r.kind === 'option',
    ),
  ).toHaveLength(2);
});
it('parent categories span subcategories and faculty names are searchable', () => {
  const values = [
    '- Theme - Computing: One',
    '- Theme - Other: Two',
    'ENG - Three',
    'MATH - Four',
    'SCI - Five',
    'ARTS - Six',
    'ENV - Seven',
    'HEALTH - Eight',
  ];
  const rows = groupedOptionRows(values, 'targeted_disciplines');
  expect(
    rows.find((row) => row.kind === 'heading' && row.label === 'Themes'),
  ).toMatchObject({ count: 2, values: values.slice(0, 2) });
  expect(
    groupedOptionRows(values, 'targeted_disciplines', 'engineering')
      .filter((row) => row.kind === 'option')
      .map((row) => row.value),
  ).toEqual(['ENG - Three']);
  expect(
    rows.filter((row) => row.kind === 'heading').map((row) => row.label),
  ).toEqual([
    'Arts',
    'Engineering',
    'Environment',
    'Health',
    'Mathematics',
    'Science',
    'Themes',
    'Computing',
    'Other',
  ]);
});
it('matches exact multiple values with OR and preserves repeated target URLs including unknowns', () => {
  expect(matchesAnySelection(['a', 'b'], ['b', 'c'])).toBe(true);
  expect(matchesAnySelection(['a'], ['A'])).toBe(false);
  expect(matchesAnySelection([], ['unknown'])).toBe(false);
  expect(matchesAnySelection([], [])).toBe(true);
  const view = readBrowseUrl(
    '?target=-+Theme+-+Computing%3A+Software+Development&target=unknown&city=a',
  );
  expect(view.filters.targeted_disciplines).toEqual([
    '- Theme - Computing: Software Development',
    'unknown',
  ]);
  const url = writeBrowseUrl('https://local.invalid/?job=123', view);
  expect(url).toContain('job=123');
  expect(readBrowseUrl(new URL(url, 'https://local.invalid').search)).toEqual(
    view,
  );
});
