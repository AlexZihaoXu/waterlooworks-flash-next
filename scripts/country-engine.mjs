import fs from 'node:fs/promises';
import { currentModel, localRequest } from './lm-client.mjs';
import { chooseReasoning, parseFinal } from './benchmark-lib.mjs';
import {
  VERSION,
  COUNTRIES,
  PROMPT,
  countryOutputSchema,
  hash,
  validateCountry,
} from './country-lib.mjs';

export async function loadCountryEngine({ concurrency = 4 } = {}) {
  const model = await currentModel('unsloth/gemma-4-26b-a4b-it');
  if (
    model.quantization?.name !== 'Q2_K_XL' ||
    model.config?.context_length !== 65536 ||
    model.config?.parallel !== 4 ||
    chooseReasoning(model.reasoning, 'auto') !== 'none'
  )
    throw new Error('model_profile');
  const codeHashes = {};
  for (const file of [
    'country.mjs',
    'country-lib.mjs',
    'country-store.mjs',
    'country-engine.mjs',
    'lm-client.mjs',
    'benchmark-lib.mjs',
    'extraction-store.mjs',
  ])
    codeHashes[file] = hash(await fs.readFile(new URL(file, import.meta.url)));
  const configuration = {
    version: VERSION,
    model,
    reasoning: 'none',
    temperature: 0,
    max_tokens: 1024,
    timeout: 120,
    concurrency,
    prompt_hash: hash(PROMPT),
    countries_hash: hash(JSON.stringify(COUNTRIES)),
    codeHashes,
  };
  return {
    configuration,
    configurationHash: hash(JSON.stringify(configuration)),
  };
}
export async function inferCountry(engine, input) {
  if (input.reason) return { status: 'unknown', reason: input.reason };
  const config = engine.configuration;
  const assertModel = async () => {
    if (
      JSON.stringify(await currentModel(config.model.id)) !==
      JSON.stringify(config.model)
    )
      throw new Error('model_changed');
  };
  await assertModel();
  const response = await localRequest(
    '/v1/chat/completions',
    {
      model: config.model.id,
      reasoning_effort: 'none',
      temperature: 0,
      max_tokens: config.max_tokens,
      store: false,
      messages: [
        { role: 'system', content: PROMPT },
        {
          role: 'user',
          content: JSON.stringify(input.fields)
            .replaceAll('<', '\\u003c')
            .replaceAll('>', '\\u003e'),
        },
      ],
      response_format: {
        type: 'json_schema',
        json_schema: {
          name: 'job_country',
          strict: true,
          schema: countryOutputSchema(input.fields),
        },
      },
    },
    config.timeout,
  );
  await assertModel();
  const choice = response.choices?.[0];
  if (
    response.model !== config.model.id ||
    response.usage?.completion_tokens_details?.reasoning_tokens !== 0 ||
    choice?.message?.reasoning ||
    choice?.message?.reasoning_content
  )
    throw new Error('model_framing');
  if (choice?.finish_reason !== 'stop')
    return { status: 'failed', reason: 'invalid_output' };
  try {
    return validateCountry(parseFinal(choice.message.content), input.fields);
  } catch {
    return { status: 'failed', reason: 'invalid_output' };
  }
}
