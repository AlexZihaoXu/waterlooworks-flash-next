import fs from 'node:fs';
import { createHash } from 'node:crypto';

export const COUNTRIES = JSON.parse(
  fs.readFileSync(
    new URL(
      '../backend/src/waterlooworks_flash_next/countries.json',
      import.meta.url,
    ),
    'utf8',
  ),
);
export const VERSION = 'job-country-v1';
export const hash = (value) => createHash('sha256').update(value).digest('hex');
export const countryCode = (value) =>
  typeof value === 'string' && Object.hasOwn(COUNTRIES, value) ? value : null;
export const boundedConfidence = (value) =>
  typeof value === 'number' &&
  Number.isFinite(value) &&
  value >= 0 &&
  value <= 1
    ? value
    : null;
export function exactEvidence(source, quote) {
  if (
    typeof source !== 'string' ||
    typeof quote !== 'string' ||
    !quote.trim() ||
    quote.length > 500
  )
    return null;
  const offset = source.indexOf(quote);
  if (offset < 0 || source.indexOf(quote, offset + 1) !== -1) return null;
  const start = Array.from(source.slice(0, offset)).length;
  return { start, end: start + Array.from(quote).length };
}
export const LOCATION_FIELDS = [
  'Job - City:',
  'Job - Province/State:',
  'Job - Country:',
  'Job - Address Line One:',
  'Job - Address Line Two:',
  'Employment Location Arrangement:',
  'Additional Employment Arrangement Location Information:',
  'Job Location (If Exact Address Unknown or Multiple Locations):',
];
export function locationAmbiguity(values) {
  const locations = [
    ...new Set(
      values
        .filter((v) => typeof v === 'string')
        .map((v) => v.trim().toLowerCase())
        .filter((v) => v && !['remote', 'hybrid', 'in-person'].includes(v)),
    ),
  ];
  // Only complete, unqualified work-location clauses; "anywhere in Canada" is scoped.
  if (
    locations.some((v) =>
      /^(?:remote[\s,:-]+)?(?:worldwide|anywhere(?: in the world)?|globally|work from anywhere)[.!]?$/.test(
        v,
      ),
    )
  )
    return 'ambiguous_location';
  if (locations.length === 1 && /^london[.!]?$/.test(locations[0]))
    return 'ambiguous_location';
  return null;
}
export function countryInput(listing, content) {
  const fields = {};
  if (typeof listing.city === 'string' && listing.city.trim())
    fields['listing:city'] = listing.city;
  for (const field of [
    ...LOCATION_FIELDS,
    'Job Summary:',
    'Job Responsibilities:',
  ])
    if (typeof content[field] === 'string' && content[field].trim())
      fields[`content:${field}`] = content[field];
  // Whole relevant fields only: oversize is a terminal unknown, never a silent truncation.
  return {
    fields,
    reason:
      Buffer.byteLength(JSON.stringify(fields)) > 24000
        ? 'input_too_large'
        : Object.keys(fields).length
          ? null
          : 'no_location_context',
  };
}
export const PROMPT = fs.readFileSync(
  new URL(
    '../backend/src/waterlooworks_flash_next/country-prompt.txt',
    import.meta.url,
  ),
  'utf8',
);
export const REASONS = [
  'location_context',
  'ambiguous_location',
  'no_location_context',
  'multiple_countries',
  'low_confidence',
];
export const OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['country', 'confidence', 'reason', 'evidence'],
  properties: {
    country: { enum: [null, ...Object.keys(COUNTRIES)] },
    confidence: { type: ['number', 'null'], minimum: 0, maximum: 1 },
    reason: { enum: REASONS },
    evidence: {
      type: 'array',
      maxItems: 3,
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['field', 'quote'],
        properties: {
          field: { type: 'string' },
          quote: { type: 'string', minLength: 1, maxLength: 500 },
        },
      },
    },
  },
};
export function countryOutputSchema(fields) {
  return {
    ...OUTPUT_SCHEMA,
    properties: {
      ...OUTPUT_SCHEMA.properties,
      evidence: {
        ...OUTPUT_SCHEMA.properties.evidence,
        items: {
          ...OUTPUT_SCHEMA.properties.evidence.items,
          properties: {
            ...OUTPUT_SCHEMA.properties.evidence.items.properties,
            field: { type: 'string', enum: Object.keys(fields) },
          },
        },
      },
    },
  };
}
export function validateCountry(answer, fields) {
  if (
    !answer ||
    Object.keys(answer).sort().join(',') !==
      'confidence,country,evidence,reason' ||
    !REASONS.includes(answer.reason) ||
    !Array.isArray(answer.evidence) ||
    answer.evidence.length > 3
  )
    throw new Error('invalid_output');
  if (answer.country === null) {
    if (
      answer.confidence !== null ||
      answer.evidence.length ||
      answer.reason === 'location_context'
    )
      throw new Error('invalid_output');
    return {
      status: 'unknown',
      country: null,
      confidence: null,
      reason: answer.reason,
      evidence: [],
    };
  }
  const code = countryCode(answer.country),
    confidence = boundedConfidence(answer.confidence);
  if (
    !code ||
    confidence === null ||
    answer.reason !== 'location_context' ||
    !answer.evidence.length
  )
    throw new Error('invalid_output');
  const evidence = answer.evidence.map((entry) => {
    if (
      !entry ||
      Object.keys(entry).sort().join(',') !== 'field,quote' ||
      !Object.hasOwn(fields, entry.field)
    )
      throw new Error('invalid_evidence');
    const span = exactEvidence(fields[entry.field], entry.quote);
    // These contexts can mention countries but are not an adequate work-location anchor.
    if (
      !span ||
      /headquarters|head office|citizenship|nationality|eligible to apply|salary|compensation|\bHQ\b/i.test(
        entry.quote,
      )
    )
      throw new Error('invalid_evidence');
    return { field: entry.field, ...span, quote_hash: hash(entry.quote) };
  });
  const explicitGlobal = Object.entries(fields)
    .filter(
      ([key]) =>
        key === 'listing:city' ||
        LOCATION_FIELDS.some((field) => key === `content:${field}`),
    )
    .map(([, value]) => value)
    .filter((value) => !/^london[.!]?$/i.test(value.trim()));
  if (
    locationAmbiguity(explicitGlobal) ||
    locationAmbiguity(answer.evidence.map((entry) => entry.quote))
  )
    return {
      status: 'unknown',
      country: null,
      confidence: null,
      reason: 'ambiguous_location',
      evidence: [],
    };
  if (confidence < 0.85)
    return {
      status: 'unknown',
      country: null,
      confidence: null,
      reason: 'low_confidence',
      evidence: [],
    };
  return {
    status: 'accepted',
    country: code,
    confidence,
    reason: 'location_context',
    evidence,
  };
}
