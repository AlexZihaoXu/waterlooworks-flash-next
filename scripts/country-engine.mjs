import fs from 'node:fs/promises';
import { OPENAI_MODEL, OPENAI_PROFILE } from './openai-bridge.mjs';
import {
  VERSION,
  COUNTRIES,
  PROMPT,
  countryOutputSchema,
  hash,
  validateCountry,
} from './country-lib.mjs';

export async function loadCountryEngine({ concurrency = 4, bridge } = {}) {
  if (
    !bridge ||
    !Number.isInteger(concurrency) ||
    concurrency < 1 ||
    concurrency > 4
  )
    throw new Error('OpenAI country engine configuration is invalid');
  const codeHashes = {};
  for (const file of [
    'country.mjs',
    'country-lib.mjs',
    'country-store.mjs',
    'country-engine.mjs',
    'openai-bridge.mjs',
    'extraction-store.mjs',
  ])
    codeHashes[file] = hash(await fs.readFile(new URL(file, import.meta.url)));
  const configuration = {
    version: VERSION,
    provider: 'openai-responses',
    providerVersion: OPENAI_PROFILE,
    model: { id: OPENAI_MODEL },
    reasoning: 'low',
    serviceTier: 'default',
    max_tokens: 4096,
    timeout: 120,
    concurrency,
    prompt_hash: hash(PROMPT),
    countries_hash: hash(JSON.stringify(COUNTRIES)),
    codeHashes,
    reusePolicy: 'compatible-origin-v1',
  };
  return {
    configuration,
    configurationHash: hash(JSON.stringify(configuration)),
    bridge,
  };
}
export async function inferCountry(engine, input, operationHash) {
  if (input.reason) return { status: 'unknown', reason: input.reason };
  const text = await engine.bridge.complete(
    operationHash,
    [
      { role: 'system', content: PROMPT },
      {
        role: 'user',
        content: JSON.stringify(input.fields)
          .replaceAll('<', '\\u003c')
          .replaceAll('>', '\\u003e'),
      },
    ],
    'job_country',
    countryOutputSchema(input.fields),
    engine.configuration.max_tokens,
  );
  try {
    return validateCountry(JSON.parse(text), input.fields);
  } catch {
    return { status: 'failed', reason: 'invalid_output' };
  }
}
