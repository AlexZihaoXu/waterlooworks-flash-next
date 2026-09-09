/** Explainable directional coverage, not a probability of suitability. */
export const KEYWORD_COVERAGE_VERSION = 'log1p-whole-terms-v2';

/** Small explicit equivalence list; pass [] for literal-only matching. */
export const KEYWORD_ALIASES: readonly (readonly string[])[] = [
  ['javascript', 'js'],
  ['node.js', 'nodejs'],
  ['postgresql', 'postgres'],
  ['kubernetes', 'k8s'],
  ['c++', 'cpp'],
  ['c#', 'c sharp'],
];

export type WeightedKeyword = Readonly<{
  keyword: string;
  occurrences: number;
  weight: number;
}>;
export type KeywordProfile = Readonly<{
  version: string;
  terms: readonly (WeightedKeyword & { readonly pattern: RegExp })[];
  totalWeight: number;
  excludedKeywords: readonly string[];
}>;

function normalize(value: string): string {
  return value.trim().replace(/\s+/g, ' ').toLowerCase();
}
function patternFor(names: readonly string[]): RegExp {
  const alternatives = [...names]
    .sort((a, b) => b.length - a.length || a.localeCompare(b))
    .map((name) =>
      name
        .split(' ')
        .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
        .join('\\s+'),
    );
  // Preserve punctuation-bearing technologies: C != C++, Java != JavaScript,
  // Node != Node.js. A trailing sentence-ending dot remains a valid boundary.
  const pattern = new RegExp(
    `${ASCII_PREFIX}(?:${alternatives.join('|')})${ASCII_SUFFIX}`,
    'giu',
  );
  if (names.every((name) => /^[\x20-\x7e]*$/.test(name))) {
    literalPrefilters.set(pattern, [...names]);
  }
  return pattern;
}

const ASCII_PREFIX = String.raw`(?<![\w+#.])`;
const ASCII_SUFFIX = String.raw`(?![\w+#]|\.\w)`;
const unicodeWord = /[\p{L}\p{N}_]/u;
const unicodeFallbacks = new WeakMap<RegExp, RegExp>();
const literalPrefilters = new WeakMap<RegExp, readonly string[]>();

function foldForPrefilter(text: string): string {
  // ASCII /iu matching also recognizes Kelvin sign (handled by lowercasing)
  // and long s. Non-ASCII keyword spellings bypass this prefilter entirely.
  return text.toLowerCase().replace(/ſ/g, 's').replace(/\s+/g, ' ');
}
function couldContain(folded: string, pattern: RegExp): boolean {
  const names = literalPrefilters.get(pattern);
  return !names || names.some((name) => folded.includes(name));
}

function nonAsciiWord(code: number | undefined): boolean {
  return (
    code !== undefined &&
    code > 127 &&
    unicodeWord.test(String.fromCodePoint(code))
  );
}
function validUnicodeBoundary(text: string, match: RegExpExecArray): boolean {
  const start = match.index;
  const end = start + match[0].length;
  const previous = text.charCodeAt(start - 1);
  const before =
    previous >= 0xdc00 && previous <= 0xdfff
      ? text.codePointAt(start - 2)
      : previous;
  return (
    !nonAsciiWord(before) &&
    !nonAsciiWord(text.codePointAt(end)) &&
    !(text[end] === '.' && nonAsciiWord(text.codePointAt(end + 1)))
  );
}
function unicodeFallback(pattern: RegExp): RegExp {
  let fallback = unicodeFallbacks.get(pattern);
  if (!fallback) {
    const body = pattern.source.slice(
      ASCII_PREFIX.length,
      -ASCII_SUFFIX.length,
    );
    fallback = new RegExp(
      String.raw`(?<![\p{L}\p{N}_+#.])${body}(?![\p{L}\p{N}_+#]|\.[\p{L}\p{N}_])`,
      'giu',
    );
    unicodeFallbacks.set(pattern, fallback);
  }
  return fallback;
}
function sourceMatches(
  text: string,
  pattern: RegExp,
  folded: string,
): RegExpExecArray[] {
  if (!couldContain(folded, pattern)) return [];
  const matches = [...text.matchAll(pattern)];
  // On a non-ASCII word boundary, rerun the ORIGINAL complete expression.
  // Merely discarding the match would change alias backtracking/overlap rules.
  return matches.some((match) => !validUnicodeBoundary(text, match))
    ? [...text.matchAll(unicodeFallback(pattern))]
    : matches;
}
function containsKeyword(
  text: string,
  pattern: RegExp,
  folded: string,
): boolean {
  if (!couldContain(folded, pattern)) return false;
  const first = text.matchAll(pattern).next();
  if (first.done) return false;
  return (
    validUnicodeBoundary(text, first.value) ||
    !text.matchAll(unicodeFallback(pattern)).next().done
  );
}

/** Prepare once per source/keyword/version change; never count extraction rows. */
export function prepareKeywordProfile(
  sourceFields: readonly string[],
  keywords: readonly string[],
  aliases: readonly (readonly string[])[] = KEYWORD_ALIASES,
): KeywordProfile {
  const canonical = new Map<string, string>();
  const spellings = new Map<string, Set<string>>();
  for (const group of aliases) {
    const names = group.map(normalize).filter(Boolean);
    if (!names.length) continue;
    const key = names[0];
    const forms = spellings.get(key) ?? new Set<string>();
    for (const name of names) {
      if (canonical.has(name) && canonical.get(name) !== key)
        throw new Error('Ambiguous keyword alias');
      canonical.set(name, key);
      forms.add(name);
    }
    spellings.set(key, forms);
  }
  const keys = [
    ...new Set(
      keywords
        .map(normalize)
        .filter(Boolean)
        .map((name) => canonical.get(name) ?? name),
    ),
  ].sort();
  const terms = keys.map((keyword) => ({
    keyword,
    occurrences: 0,
    weight: 0,
    pattern: patternFor([...(spellings.get(keyword) ?? [keyword])]),
  }));
  for (const text of sourceFields) {
    const folded = foldForPrefilter(text);
    const spans: { start: number; end: number; term: number }[] = [];
    terms.forEach((term, index) => {
      for (const match of sourceMatches(text, term.pattern, folded)) {
        spans.push({
          start: match.index,
          end: match.index + match[0].length,
          term: index,
        });
      }
    });
    // Deterministic leftmost-longest selection: nested/overlapping phrases do
    // not multiply source frequency. Distinct mentions elsewhere still count.
    spans.sort((a, b) => a.start - b.start || b.end - a.end || a.term - b.term);
    let end = -1;
    for (const span of spans) {
      if (span.start < end) continue;
      terms[span.term].occurrences += 1;
      end = span.end;
    }
  }
  const usable = terms
    .filter((term) => term.occurrences > 0)
    .map((term) =>
      Object.freeze({ ...term, weight: Math.log1p(term.occurrences) }),
    );
  return Object.freeze({
    version: KEYWORD_COVERAGE_VERSION,
    terms: Object.freeze(usable),
    totalWeight: usable.reduce((sum, term) => sum + term.weight, 0),
    excludedKeywords: Object.freeze(
      terms.filter((term) => !term.occurrences).map((term) => term.keyword),
    ),
  });
}

/** Each target keyword earns its weight at most once in the comparison text. */
export function scoreKeywordCoverage(text: string, profile: KeywordProfile) {
  const folded = foldForPrefilter(text);
  return scoreFromMatches(profile, (index) =>
    containsKeyword(text, profile.terms[index].pattern, folded),
  );
}

function scoreFromMatches(
  profile: KeywordProfile,
  isMatched: (index: number) => boolean,
) {
  const matched: WeightedKeyword[] = [],
    missing: WeightedKeyword[] = [];
  let matchedWeight = 0;
  for (let index = 0; index < profile.terms.length; index++) {
    const term = profile.terms[index];
    const detail = {
      keyword: term.keyword,
      occurrences: term.occurrences,
      weight: term.weight,
    };
    // matchAll clones the regex: repeated calls never share mutable lastIndex.
    if (isMatched(index)) {
      matched.push(detail);
      matchedWeight += term.weight;
    } else missing.push(detail);
  }
  return {
    score:
      profile.totalWeight > 0
        ? Math.min(100, (100 * matchedWeight) / profile.totalWeight)
        : null,
    matchedWeight,
    totalWeight: profile.totalWeight,
    matched,
    missing,
  };
}

export type KeywordSearch = Readonly<{
  profiles: readonly KeywordProfile[];
  matchers: readonly RegExp[];
  termIndexes: readonly (readonly number[])[];
}>;

/** Build once per target-set change; identical keyword matchers are shared. */
export function prepareKeywordSearch(
  profiles: readonly KeywordProfile[],
): KeywordSearch {
  const keys = new Map<string, number>();
  const matchers: RegExp[] = [];
  const termIndexes = profiles.map((profile) =>
    Object.freeze(
      profile.terms.map((term) => {
        // Matcher identity includes alias configuration, not just canonical name.
        const key = term.pattern.source;
        let index = keys.get(key);
        if (index === undefined) {
          index = matchers.length;
          keys.set(key, index);
          matchers.push(term.pattern);
        }
        return index;
      }),
    ),
  );
  return Object.freeze({
    profiles: Object.freeze([...profiles]),
    matchers: Object.freeze(matchers),
    termIndexes: Object.freeze(termIndexes),
  });
}

/** Scan each distinct keyword once for ALL targets; include full explanations. */
export function scoreKeywordCoverageBatch(text: string, search: KeywordSearch) {
  const folded = foldForPrefilter(text);
  const hits = Uint8Array.from(search.matchers, (pattern) =>
    Number(containsKeyword(text, pattern, folded)),
  );
  return search.profiles.map((profile, index) =>
    scoreFromMatches(
      profile,
      (term) => hits[search.termIndexes[index][term]] === 1,
    ),
  );
}
