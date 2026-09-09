import path from 'node:path';

export function benchmarkTable(summary) {
  const salary = summary.results.filter((r) => r.mode === 'salary');
  const keywords = summary.results.filter((r) => r.mode === 'keywords');
  const sum = (rows, f) => rows.reduce((n, r) => n + f(r), 0);
  return [
    [
      'Salary amounts/currency/period match, whole posting',
      `${summary.salaryValueMatches}/${salary.length}`,
    ],
    [
      'Salary response JSON parses',
      `${sum(salary, (r) => Number(r.jsonValid))}/${salary.length}`,
    ],
    [
      'Salary quotes copied exactly, entrywise',
      `${sum(salary, (r) => r.quotedRates ?? 0)}/${sum(salary, (r) => r.rates ?? 0)}`,
    ],
    [
      'Keyword response JSON parses',
      `${sum(keywords, (r) => Number(r.jsonValid))}/${keywords.length}`,
    ],
    [
      'Keyword reference-name/alias coverage',
      `${summary.keywordMatches}/${summary.keywordReferences}`,
    ],
    [
      'Keyword exact quote + mention compliance, entrywise',
      `${sum(keywords, (r) => r.quotedTerms ?? 0)}/${sum(keywords, (r) => r.terms ?? 0)}`,
    ],
    [
      'Duplicate keyword entries (case-insensitive)',
      String(sum(keywords, (r) => r.duplicates ?? 0)),
    ],
  ].map(([Metric, Result]) => ({ Metric, Result }));
}

export function decimalKey(value) {
  if (
    typeof value !== 'string' ||
    value.length > 64 ||
    !/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value)
  )
    throw new Error('Invalid decimal string');
  return value.includes('.')
    ? value.replace(/0+$/, '').replace(/\.$/, '')
    : value;
}
export function salaryKey([currency, minimum, maximum, period]) {
  if (!(
    currency === null ||
    (typeof currency === 'string' && /^[A-Z]{3}$/.test(currency))
  ))
    throw new Error('Invalid currency');
  if (![null, 'hour', 'day', 'week', 'month', 'year'].includes(period))
    throw new Error('Invalid period');
  return JSON.stringify([
    currency,
    minimum === null ? null : decimalKey(minimum),
    maximum === null ? null : decimalKey(maximum),
    period,
  ]);
}
export function chooseReasoning(supported, requested) {
  if (requested === 'auto')
    return supported.includes('off')
      ? 'none'
      : supported.includes('low')
        ? 'low'
        : undefined;
  if (!supported.includes(requested === 'none' ? 'off' : requested))
    throw new Error(
      'Loaded model does not support requested reasoning setting',
    );
  return requested;
}
const normalize = (value) => value.toLowerCase().replace(/\s+/g, ' ').trim();
export function lexicalCoverage(names, aliases) {
  const found = new Set(names.map(normalize));
  const missing = aliases.filter(
    (group) => !group.some((alias) => found.has(normalize(alias))),
  );
  return {
    matched: aliases.length - missing.length,
    total: aliases.length,
    missing,
  };
}
export function quoteCheck(source, mention, evidence) {
  return (
    typeof source === 'string' &&
    typeof mention === 'string' &&
    typeof evidence === 'string' &&
    mention.length > 0 &&
    evidence.length > 0 &&
    source.includes(evidence) &&
    evidence.includes(mention)
  );
}
export function parseFinal(text) {
  if (typeof text !== 'string' || /<\/?think>|<\|/.test(text))
    throw new Error('Final text contains invalid framing');
  const value = JSON.parse(
    text.trim().replace(/^```(?:json)?\s*\n([\s\S]*?)\n```$/i, '$1'),
  );
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Expected one JSON object');
  return value;
}
export function safeDatasetPath(value) {
  if (
    value.replaceAll('\\', '/').split('/').at(-1).toLowerCase() ===
    '.credentials.json'
  )
    throw new Error('Credentials are not benchmark inputs');
  return path.resolve(value);
}
export function optionsFromArgs(args) {
  const options = {
    dataset: '.experiment/benchmark/cases.json',
    concurrency: 4,
    limit: 6,
    maxTokens: 4096,
    timeout: 300,
    mode: 'both',
    reasoning: 'auto',
    model: null,
    dryRun: false,
    help: false,
  };
  const names = {
    '--dataset': 'dataset',
    '--concurrency': 'concurrency',
    '--limit': 'limit',
    '--max-tokens': 'maxTokens',
    '--timeout': 'timeout',
    '--mode': 'mode',
    '--reasoning': 'reasoning',
    '--model': 'model',
  };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--dry-run') {
      options.dryRun = true;
      continue;
    }
    if (args[i] === '--help') {
      options.help = true;
      continue;
    }
    const name = names[args[i]];
    if (!name || !args[i + 1] || args[i + 1].startsWith('--'))
      throw new Error('Unknown or incomplete benchmark option; use --help');
    options[name] = args[++i];
  }
  for (const [key, min, max] of [
    ['concurrency', 1, 4],
    ['limit', 1, 100],
    ['maxTokens', 256, 8192],
    ['timeout', 10, 600],
  ]) {
    options[key] = Number(options[key]);
    if (
      !Number.isInteger(options[key]) ||
      options[key] < min ||
      options[key] > max
    )
      throw new Error(`Invalid ${key}: expected ${min}–${max}`);
  }
  if (
    !['both', 'salary', 'keywords'].includes(options.mode) ||
    !['auto', 'none', 'low', 'medium', 'high'].includes(options.reasoning)
  )
    throw new Error('Invalid mode or reasoning setting');
  safeDatasetPath(options.dataset);
  return options;
}
export function validateDataset(data) {
  if (
    data?.version !== 1 ||
    !Array.isArray(data.cases) ||
    data.cases.length === 0 ||
    data.cases.length > 1000
  )
    throw new Error('Expected dataset version 1 with 1–1000 cases');
  const ids = new Set();
  for (const c of data.cases) {
    if (typeof c.id !== 'string' || !/^\d+$/.test(c.id) || ids.has(c.id))
      throw new Error('Case IDs must be unique digit strings');
    ids.add(c.id);
    if (
      !c.fields ||
      typeof c.fields !== 'object' ||
      Array.isArray(c.fields) ||
      !Object.keys(c.fields).length ||
      !Object.entries(c.fields).every(
        ([k, v]) =>
          k.length > 0 &&
          k.length <= 100 &&
          typeof v === 'string' &&
          v.length > 0 &&
          v.length <= 100000,
      )
    )
      throw new Error('Invalid saved fields');
    if (
      !Array.isArray(c.deterministicSkills) ||
      !c.deterministicSkills.every((v) => typeof v === 'string')
    )
      throw new Error('Expected deterministicSkills array');
    if (
      !Array.isArray(c.reference?.salary) ||
      !Array.isArray(c.reference?.keywords) ||
      !c.reference.keywords.every(
        (group) =>
          Array.isArray(group) &&
          group.length &&
          group.every((v) => typeof v === 'string' && v.trim()),
      )
    )
      throw new Error('Invalid reference annotations');
    c.reference.salary.forEach((rate) => {
      if (!Array.isArray(rate) || rate.length !== 4)
        throw new Error('Salary reference needs four values');
      salaryKey(rate);
    });
  }
  return data.cases;
}
export function evaluateAnswer(mode, answer, sample) {
  if (mode === 'salary') {
    if (!Array.isArray(answer.base_rates) || !Array.isArray(answer.skills))
      throw new Error('Invalid compensation/skills response shape');
    const rates = answer.base_rates;
    let salaryValuesMatch = false;
    let canonicalValues = true;
    try {
      const actual = rates
        .map((r) =>
          salaryKey([
            r.currency ?? null,
            r.minimum ?? null,
            r.maximum ?? null,
            r.period ?? null,
          ]),
        )
        .sort();
      salaryValuesMatch =
        JSON.stringify(actual) ===
        JSON.stringify(sample.reference.salary.map(salaryKey).sort());
    } catch {
      canonicalValues = false;
    }
    const quotedRates = rates.filter((r) =>
      quoteCheck(
        sample.fields[r.evidence?.field],
        r.evidence?.text,
        r.evidence?.text,
      ),
    ).length;
    return {
      salaryValuesMatch,
      canonicalValues,
      rates: rates.length,
      quotedRates,
      allSalaryQuotesValid: quotedRates === rates.length,
      emptyRates: rates.length === 0,
    };
  }
  if (
    !Array.isArray(answer.terms) ||
    !answer.terms.every(
      (t) =>
        t &&
        ['term', 'mention', 'field', 'context', 'evidence'].every(
          (k) => typeof t[k] === 'string',
        ) &&
        t.term.trim() &&
        ['required', 'preferred', 'mentioned', 'negated'].includes(t.context),
    )
  )
    throw new Error('Invalid keyword response shape');
  const terms = answer.terms;
  return {
    terms: terms.length,
    quotedTerms: terms.filter((t) =>
      quoteCheck(sample.fields[t.field], t.mention, t.evidence),
    ).length,
    duplicates:
      terms.length - new Set(terms.map((t) => normalize(t.term))).size,
    coverage: lexicalCoverage(
      terms.flatMap((t) => [t.term, t.mention]),
      sample.reference.keywords,
    ),
  };
}
