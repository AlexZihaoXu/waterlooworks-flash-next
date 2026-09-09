import { expect, it } from 'vitest';
import {
  prepareKeywordProfile,
  scoreKeywordCoverage,
  prepareKeywordSearch,
  scoreKeywordCoverageBatch,
} from '../../src/lib/keywordCoverage';

it('uses log1p frequency and earns each keyword weight only once', () => {
  const profile = prepareKeywordProfile(
    ['alpha alpha alpha alpha beta'],
    ['alpha', 'beta'],
  );
  expect(profile.totalWeight).toBeCloseTo(Math.log(5) + Math.log(2));
  const result = scoreKeywordCoverage('ALPHA alpha', profile);
  expect(result.score).toBeCloseTo(
    (100 * Math.log(5)) / (Math.log(5) + Math.log(2)),
  );
  expect(result.matched.map((k) => k.keyword)).toEqual(['alpha']);
  expect(result.missing.map((k) => k.keyword)).toEqual(['beta']);
});
it('counts source occurrences, not duplicate extraction passes', () => {
  const profile = prepareKeywordProfile(
    ['alpha alpha', 'alpha'],
    ['alpha', 'ALPHA', 'alpha'],
  );
  expect(profile.terms).toHaveLength(1);
  expect(profile.terms[0].occurrences).toBe(3);
  expect(scoreKeywordCoverage('alpha', profile).score).toBe(100);
});
it('uses whole technical terms rather than substrings', () => {
  const profile = prepareKeywordProfile(
    ['Java JavaScript C C++ C# Node.js .NET'],
    ['Java', 'JavaScript', 'C', 'C++', 'C#', 'Node.js', '.NET'],
  );
  const result = scoreKeywordCoverage('JavaScript C++ Node.js. .NET', profile);
  expect(result.matched.map((k) => k.keyword).sort()).toEqual([
    '.net',
    'c++',
    'javascript',
    'node.js',
  ]);
  expect(scoreKeywordCoverage('JavaScript C++ Node.js. .NET', profile)).toEqual(
    result,
  );
});
it('merges known aliases before counting and supports an explicit exact-only mode', () => {
  const profile = prepareKeywordProfile(
    ['JavaScript JS'],
    ['JS', 'javascript'],
  );
  expect(profile.terms).toHaveLength(1);
  expect(profile.terms[0].occurrences).toBe(2);
  expect(scoreKeywordCoverage('js', profile).score).toBe(100);
  expect(
    scoreKeywordCoverage(
      'js',
      prepareKeywordProfile(['JavaScript'], ['JavaScript'], []),
    ).score,
  ).toBe(0);
});
it('counts overlapping terms only once using leftmost-longest matching', () => {
  const profile = prepareKeywordProfile(
    ['SQL queries; SQL.'],
    ['SQL', 'SQL queries'],
  );
  expect(profile.terms.map((k) => [k.keyword, k.occurrences])).toEqual([
    ['sql', 1],
    ['sql queries', 1],
  ]);
  const nested = prepareKeywordProfile(['SQL queries'], ['SQL', 'SQL queries']);
  expect(nested.terms.map((k) => k.keyword)).toEqual(['sql queries']);
});
it('matches phrases across whitespace but never counts across source fields', () => {
  expect(
    prepareKeywordProfile(['alpha\n beta'], ['alpha beta']).terms[0]
      .occurrences,
  ).toBe(1);
  expect(
    prepareKeywordProfile(['alpha', 'beta'], ['alpha beta']).totalWeight,
  ).toBe(0);
});
it('returns unavailable for no usable keywords, not a fabricated zero', () => {
  expect(
    scoreKeywordCoverage('alpha', prepareKeywordProfile([], [])).score,
  ).toBeNull();
  expect(
    scoreKeywordCoverage('alpha', prepareKeywordProfile(['beta'], ['alpha']))
      .score,
  ).toBeNull();
  expect(
    scoreKeywordCoverage('', prepareKeywordProfile(['alpha'], ['alpha'])).score,
  ).toBe(0);
});
it('is directional, has a perfect self-match, and rejects conflicting aliases', () => {
  const a = prepareKeywordProfile(['alpha'], ['alpha']);
  const b = prepareKeywordProfile(['alpha beta'], ['alpha', 'beta']);
  expect(scoreKeywordCoverage('alpha', a).score).toBe(100);
  expect(scoreKeywordCoverage('alpha beta', b).score).toBe(100);
  expect(scoreKeywordCoverage('alpha beta', a).score).toBe(100);
  expect(scoreKeywordCoverage('alpha', b).score).toBe(50);
  expect(() =>
    prepareKeywordProfile(
      ['alpha'],
      ['alpha'],
      [
        ['alpha', 'a'],
        ['beta', 'a'],
      ],
    ),
  ).toThrow('Ambiguous keyword alias');
});

it('keeps Unicode boundaries and alias backtracking equivalent to the original matcher', () => {
  const profile = prepareKeywordProfile(
    ['alpha; a b; a'],
    ['alpha', 'a'],
    [['a b', 'a']],
  );
  for (const [text, expected] of [
    ['éalpha', false],
    ['alphaλ', false],
    ['𐐀alpha', false],
    ['alpha.λ', false],
    ['—alpha', true],
    ['😀alpha.', true],
  ] as const) {
    expect(
      scoreKeywordCoverage(text, profile).matched.some(
        (k) => k.keyword === 'alpha',
      ),
    ).toBe(expected);
  }
  // The longer alias has an invalid Unicode boundary, but the shorter alias
  // at the same position remains valid: the original regex backtracks to it.
  expect(
    scoreKeywordCoverage('a bλ', profile).matched.map((k) => k.keyword),
  ).toEqual(['a b']);
  expect(
    prepareKeywordProfile(['a bλ'], ['a'], [['a b', 'a']]).terms[0].occurrences,
  ).toBe(1);
  const unicode = prepareKeywordProfile(['Σ K S'], ['Σ', 'K', 'S']);
  expect(scoreKeywordCoverage('ς K ſ', unicode).score).toBe(100);
});

it('batch scoring preserves full single-profile results, nulls, duplicates and alias scope', () => {
  const profiles = [
    prepareKeywordProfile(['alpha alpha beta'], ['alpha', 'beta']),
    prepareKeywordProfile(['beta gamma'], ['beta', 'gamma']),
    prepareKeywordProfile([], []),
    prepareKeywordProfile(['JavaScript'], ['JavaScript']),
    prepareKeywordProfile(['JavaScript'], ['JavaScript'], []),
  ];
  const search = prepareKeywordSearch(profiles);
  expect(search.matchers.length).toBeLessThan(
    profiles.reduce((n, p) => n + p.terms.length, 0),
  );
  for (const text of [
    'alpha beta',
    'gamma JS',
    '',
    'éalpha betaλ',
    'alpha beta',
  ]) {
    expect(scoreKeywordCoverageBatch(text, search)).toEqual(
      profiles.map((p) => scoreKeywordCoverage(text, p)),
    );
  }
  expect(scoreKeywordCoverageBatch('alpha', prepareKeywordSearch([]))).toEqual(
    [],
  );
});
