import fs from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { currentModel, localRequest } from './lm-client.mjs';
import { chooseReasoning, parseFinal } from './benchmark-lib.mjs';
import {
  SHARED_JOB_PREFIX_VERSION,
  sharedExtractionMessages,
} from './shared-job-prompt.mjs';
import {
  sourceFields,
  outputSchema,
  validateSpans,
  EXTRACTOR_VERSION,
} from './extraction-lib.mjs';

export function extractionProfile(sharedJobPrefix = false) {
  return sharedJobPrefix
    ? {
        profile: 'v8-shared-job-prefix',
        prefixVersion: SHARED_JOB_PREFIX_VERSION,
        scheduling: 'posting-affine-v1',
      }
    : { profile: 'v7' };
}
export function extractionMessages(
  fields,
  selected,
  instructions,
  sharedJobPrefix = false,
) {
  return sharedJobPrefix
    ? sharedExtractionMessages(fields, selected, instructions)
    : [
        { role: 'system', content: instructions },
        {
          role: 'user',
          content: JSON.stringify(selected)
            .replaceAll('<', '\\u003c')
            .replaceAll('>', '\\u003e'),
        },
      ];
}
export function withinContextBudget(body, maxTokens, contextLength) {
  return (
    Number.isSafeInteger(contextLength) &&
    contextLength > 0 &&
    Number.isSafeInteger(maxTokens) &&
    maxTokens > 0 &&
    Buffer.byteLength(JSON.stringify(body)) + maxTokens + 512 <= contextLength
  );
}

export const hash = (value) => createHash('sha256').update(value).digest('hex');
export const MODEL = 'unsloth/gemma-4-26b-a4b-it';
export async function loadEngine({
  provider = 'local-benchmark',
  maxTokens = 4096,
  timeout = 300,
  concurrency = 4,
  strictModel = true,
  modelId,
  sharedJobPrefix = false,
} = {}) {
  // The paid OpenAI Responses provider was removed with its Python bridge;
  // production extraction runs through the Pi sidecar (docs/pi-inference.md).
  if (provider !== 'local-benchmark')
    throw new Error('Unsupported extraction provider');
  const model = await currentModel(strictModel ? MODEL : modelId);
  if (
    strictModel &&
    (model.quantization?.name !== 'Q2_K_XL' ||
      model.config.context_length !== 65536 ||
      model.config.parallel !== 4)
  )
    throw new Error(
      'Final tested model/quantization/context/four slots are not loaded',
    );
  if (
    !Number.isInteger(model.config?.context_length) ||
    model.config.context_length < 1
  )
    throw new Error('Loaded context is unavailable');
  const reasoning = chooseReasoning(model.reasoning, 'auto');
  if (strictModel && reasoning !== 'none')
    throw new Error('Tested profile requires supported reasoning off');
  const prompts = {};
  for (const mode of ['salary', 'keywords'])
    prompts[mode] = await fs.readFile(
      new URL(`../benchmarks/optimized/v7-${mode}.txt`, import.meta.url),
      'utf8',
    );
  const files = [
    'benchmark-lib.mjs',
    'extract.mjs',
    'extraction-lib.mjs',
    'lm-client.mjs',
    'extraction-engine.mjs',
    'extraction-store.mjs',
    ...(sharedJobPrefix ? ['shared-job-prompt.mjs'] : []),
  ];
  const codeHashes = Object.fromEntries(
    await Promise.all(
      files.map(async (name) => [
        name,
        hash(await fs.readFile(new URL(name, import.meta.url))),
      ]),
    ),
  );
  const configuration = {
    version: EXTRACTOR_VERSION,
    ...extractionProfile(sharedJobPrefix),
    model,
    reasoning,
    maxTokens,
    timeout,
    concurrency,
    temperature: 0,
    structuredOutput: true,
    codeHashes,
    promptHashes: Object.fromEntries(
      Object.entries(prompts).map(([k, v]) => [k, hash(v)]),
    ),
  };
  return {
    configuration,
    configurationHash: hash(JSON.stringify(configuration)),
    prompts,
  };
}
export function extractionParts(fields, mode = 'both') {
  return [
    ...(mode === 'keywords' ? [] : [{ mode: 'salary', part: 'all' }]),
    ...(mode === 'salary'
      ? []
      : [
          { mode: 'keywords', part: 'all' },
          ...Object.keys(sourceFields(fields, 'keywords')).map((part) => ({
            mode: 'keywords',
            part,
          })),
        ]),
  ];
}
export async function extractPart(engine, fields, mode, part, operationHash) {
  if (
    JSON.stringify(await currentModel(engine.configuration.model.id)) !==
    JSON.stringify(engine.configuration.model)
  )
    throw new Error('model-changed');
  const all = sourceFields(fields, mode);
  const selected = part === 'all' ? all : { [part]: all[part] };
  if (Object.values(selected).some((v) => !v))
    throw new Error('source-part-missing');
  if (!Object.keys(selected).length)
    return {
      status: 'accepted',
      accepted: [],
      rejected: [],
      usage: {},
      seconds: 0,
      origin: 'empty-source',
    };
  const schema = outputSchema(selected, mode);
  const body = {
    model: engine.configuration.model.id,
    messages: extractionMessages(
      fields,
      selected,
      engine.prompts[mode],
      engine.configuration.profile === 'v8-shared-job-prefix',
    ),
    ...(engine.configuration.reasoning
      ? { reasoning_effort: engine.configuration.reasoning }
      : {}),
    temperature: 0,
    max_tokens: engine.configuration.maxTokens,
    store: false,
    response_format: {
      type: 'json_schema',
      json_schema: { name: `extract_${mode}`, strict: true, schema },
    },
  };
  if (
    !withinContextBudget(
      body,
      engine.configuration.maxTokens,
      engine.configuration.model.config.context_length,
    )
  )
    return {
      status: 'rejected',
      accepted: [],
      rejected: ['context-budget'],
      usage: {},
      seconds: 0,
    };
  const start = performance.now();
  let text;
  let choice;
  let usage = {};
  {
    const response = await localRequest(
      '/v1/chat/completions',
      body,
      engine.configuration.timeout,
    );
    choice = response.choices?.[0];
    usage = response.usage ?? {};
    if (
      engine.configuration.reasoning === 'none' &&
      (usage.completion_tokens_details?.reasoning_tokens !== 0 ||
        choice?.message?.reasoning ||
        choice?.message?.reasoning_content)
    )
      throw new Error('reasoning-not-off');
    text = choice?.message?.content;
  }
  const result = {
    status: 'rejected',
    accepted: [],
    rejected: [],
    usage: {
      prompt_tokens: usage.prompt_tokens ?? null,
      completion_tokens: usage.completion_tokens ?? null,
      reasoning_tokens:
        usage.completion_tokens_details?.reasoning_tokens ?? null,
    },
    seconds: (performance.now() - start) / 1000,
  };
  if (choice?.finish_reason !== 'stop') {
    result.rejected.push('incomplete-output');
    return result;
  }
  try {
    const answer = parseFinal(text);
    result.jsonValid = true;
    result.answer = answer;
    const validated = validateSpans(answer, selected, mode);
    Object.assign(result, validated);
    if (mode === 'salary' && validated.accepted.some((q) => !q.rates.length)) {
      result.rejected.push('unparsed-pay-quote');
      // Salary is atomic: retain no rates from an incomplete/ambiguous response.
      result.accepted = [];
    }
    if (mode === 'salary' && result.rejected.length) result.accepted = [];
    result.status = result.rejected.length
      ? result.accepted.length
        ? 'partial'
        : 'rejected'
      : 'accepted';
  } catch {
    result.accepted = [];
    result.rejected = ['invalid-output'];
  }
  return result;
}
