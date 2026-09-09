import { decimalKey } from './benchmark-lib.mjs';

export const EXTRACTOR_VERSION = 'source-spans-v7';
export const normalizeTerm = (text) =>
  text.replace(/\s+/g, ' ').trim().toLowerCase();
export function deduplicateTerms(terms) {
  const seen = new Set();
  return terms.filter((t) => {
    const key = normalizeTerm(t);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
export function locateTerm(source, term) {
  if (
    typeof source !== 'string' ||
    typeof term !== 'string' ||
    !term.trim() ||
    term.length > 120
  )
    return [];
  const spans = [];
  for (
    let start = source.indexOf(term);
    start >= 0;
    start = source.indexOf(term, start + 1)
  ) {
    const end = start + term.length;
    if (
      !/[\p{L}\p{N}_]/u.test(source[start - 1] ?? '') &&
      !/[\p{L}\p{N}_+#]/u.test(source[end] ?? '')
    )
      spans.push({ start, end });
  }
  return spans;
}
const CURRENCY =
  '(?:CAD|USD|EUR|GBP|AUD|CHF|JPY|INR|CNY|Canadian dollars?|US dollars?|U\\.S\\. dollars?)';
const NUMBER =
  '(?:[1-9][0-9]{0,2}(?:,[0-9]{3})+|[0-9]+)(?:\\.[0-9]{1,2})?[kK]?';
const MONEY = new RegExp(
  `(?<![\\w.,])(?:(?<c1>${CURRENCY})\\s*)?(?<dollar>\\$)?(?<n1>${NUMBER})(?:\\s*(?:-|–|—|to)\\s*(?:(?<c2>${CURRENCY})\\s*)?\\$?(?<n2>${NUMBER}))?(?:\\s*(?<c3>${CURRENCY}))?(?![\\w.,][0-9])(?<plus>\\+)?(?:\\s*\\*?\\s*(?<period>/\\s*(?:hour|hr|h|day|week|wk|month|mo|year|yr)|per\\s+(?:hour|day|week|month|year)|an?\\s+(?:hour|day|week|month|year)|hourly|daily|weekly|monthly|annually))?(?:\\s*(?<c4>${CURRENCY}))?`,
  'gi',
);
const PERIOD = {
  hour: 'hour',
  hr: 'hour',
  h: 'hour',
  hourly: 'hour',
  day: 'day',
  daily: 'day',
  week: 'week',
  wk: 'week',
  weekly: 'week',
  month: 'month',
  mo: 'month',
  monthly: 'month',
  year: 'year',
  yr: 'year',
  annually: 'year',
};
function amount(raw) {
  let v = raw.replaceAll(',', '');
  if (/k$/i.test(v)) {
    v = v.slice(0, -1);
    const [a, b = ''] = v.split('.');
    v = (BigInt(a) * 1000n + BigInt(b.padEnd(3, '0'))).toString();
  }
  return decimalKey(v);
}
export function parsePayQuote(text) {
  if (typeof text !== 'string' || text.length > 8000) return [];
  // Non-base financial amounts are never promoted to base salary.
  if (
    /\b(revenue|valuation|funding|budget|stipend|allowance|bonus|equity|stock options?|reimbursement|pension contribution)\b/i.test(
      text,
    )
  )
    return [];
  const result = [];
  for (const match of text.matchAll(MONEY)) {
    const g = match.groups;
    if (!g.dollar && !g.c1 && !g.c2 && !g.c3 && !g.period) continue;
    if (
      /^[\p{L}\p{N}%]|^[.,][0-9]/u.test(
        text.slice(match.index + match[0].length),
      )
    )
      continue;
    const codes = [g.c1, g.c2, g.c3, g.c4]
      .filter(Boolean)
      .map((s) =>
        /canadian/i.test(s)
          ? 'CAD'
          : /^u\.?s/i.test(s)
            ? 'USD'
            : s.toUpperCase(),
      );
    if (new Set(codes).size > 1) continue;
    const currency = codes[0] ?? null;
    const before = text.slice(0, match.index);
    const insideParentheses = before.lastIndexOf('(') > before.lastIndexOf(')');
    // A foreign-currency parenthetical following a primary outside rate is not a second base rate.
    if (
      insideParentheses &&
      result.some(
        (r) =>
          r.currency &&
          currency &&
          r.currency !== currency &&
          !text.slice(0, r.source_start).includes('('),
      )
    )
      continue;
    if (
      /^\s*(?:million|billion|thousand|m\b|b\b)/i.test(
        text.slice(match.index + match[0].length),
      )
    )
      continue;
    const period = g.period
      ? (PERIOD[
          g.period.toLowerCase().replace(/^(?:\/\s*|per\s+|an?\s+)/, '')
        ] ?? null)
      : null;
    const a = amount(g.n1),
      b = g.n2 ? amount(g.n2) : a;
    const scaled = (v) =>
      BigInt(v.split('.')[0]) * 100n +
      BigInt((v.split('.')[1] ?? '').padEnd(2, '0'));
    if (scaled(a) > scaled(b)) continue;
    const prefix = text.slice(Math.max(0, match.index - 24), match.index);
    const upper = /(?:up to|less than|under|<=|<)\s*$/i.test(prefix);
    const lower =
      Boolean(g.plus) ||
      /(?:at least|starting at|more than|over|>=|>)\s*$/i.test(prefix) ||
      (!g.n2 && /from\s*$/i.test(prefix));
    if (g.n2 && (upper || lower)) continue;
    result.push({
      minimum: upper ? null : a,
      maximum: lower ? null : b,
      currency,
      period,
      bound_type: upper
        ? 'maximum'
        : lower
          ? 'minimum'
          : g.n2
            ? 'range'
            : 'fixed',
      minimum_inclusive: upper
        ? null
        : !/(?:more than|over|(?<![<>=])>)\s*$/i.test(prefix),
      maximum_inclusive: lower
        ? null
        : !/(?:less than|under|(?<![<>=])<)\s*$/i.test(prefix),
      source_start: match.index,
      source_end: match.index + match[0].length,
    });
  }
  return result;
}
export function sourceFields(fields, mode) {
  const entries = Object.entries(fields).filter(
    ([name, text]) =>
      typeof text === 'string' &&
      text.trim() &&
      (mode === 'salary'
        ? /compensation|benefits|salary|job summary|responsibilit/i.test(name)
        : /job summary|required skills|responsibilit/i.test(name)),
  );
  return Object.fromEntries(
    entries.map(([name, text], i) => [`f${i}`, { name, text }]),
  );
}
export function outputSchema(fields, mode) {
  const key = mode === 'salary' ? 'quotes' : 'terms';
  return {
    type: 'object',
    additionalProperties: false,
    properties: {
      [key]: {
        type: 'array',
        maxItems: mode === 'salary' ? 24 : 160,
        items: {
          type: 'object',
          additionalProperties: false,
          properties: {
            field: { type: 'string', enum: Object.keys(fields) },
            text: {
              type: 'string',
              minLength: 1,
              maxLength: mode === 'salary' ? 8000 : 120,
            },
          },
          required: ['field', 'text'],
        },
      },
    },
    required: [key],
  };
}
export function validateSpans(answer, fields, mode) {
  const key = mode === 'salary' ? 'quotes' : 'terms';
  if (
    !Array.isArray(answer?.[key]) ||
    Object.keys(answer).length !== 1 ||
    answer[key].length > (mode === 'salary' ? 24 : 160)
  )
    throw new Error('Invalid output shape');
  const accepted = [],
    rejected = [];
  for (const entry of answer[key]) {
    const source = fields[entry?.field];
    if (
      !source ||
      typeof entry.text !== 'string' ||
      !entry.text.trim() ||
      entry.text.length > (mode === 'salary' ? 8000 : 120) ||
      Object.keys(entry).sort().join(',') !== 'field,text'
    ) {
      rejected.push('invalid-entry');
      continue;
    }
    const spans =
      mode === 'salary'
        ? source.text.includes(entry.text)
          ? [
              {
                start: source.text.indexOf(entry.text),
                end: source.text.indexOf(entry.text) + entry.text.length,
              },
            ]
          : []
        : locateTerm(source.text, entry.text);
    if (!spans.length) {
      rejected.push('not-exact-source');
      continue;
    }
    if (accepted.some((t) => t.field === source.name && t.text === entry.text))
      continue;
    if (
      mode === 'salary' &&
      source.text.indexOf(entry.text, spans[0].start + 1) >= 0
    ) {
      rejected.push('ambiguous-pay-quote');
      continue;
    }
    if (
      mode === 'salary' &&
      accepted.some(
        (t) =>
          t.field === source.name &&
          t.spans[0].start < spans[0].end &&
          spans[0].start < t.spans[0].end,
      )
    ) {
      rejected.push('overlapping-pay-quotes');
      continue;
    }
    accepted.push({
      field: source.name,
      text: entry.text,
      spans,
      ...(mode === 'salary' ? { rates: parsePayQuote(entry.text) } : {}),
    });
  }
  if (mode === 'keywords') {
    for (const entry of [...accepted]) {
      const parts = entry.text.split('/');
      if (
        parts.length < 2 ||
        !parts.every((p) => /^[A-Z][A-Za-z0-9+#.-]{0,30}$/.test(p))
      )
        continue;
      const source = Object.values(fields).find((f) => f.name === entry.field);
      for (const text of parts) {
        const spans = locateTerm(source.text, text);
        if (
          spans.length &&
          !accepted.some((t) => t.field === entry.field && t.text === text)
        )
          accepted.push({ field: entry.field, text, spans });
      }
    }
  }
  return { accepted, rejected };
}
