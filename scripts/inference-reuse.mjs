import fs from 'node:fs';
import { createHash } from 'node:crypto';

export const REUSE_CONTRACT = JSON.parse(
  fs.readFileSync(
    new URL(
      '../backend/src/waterlooworks_flash_next/inference-reuse-contract.json',
      import.meta.url,
    ),
    'utf8',
  ),
);
const hash = (text) => createHash('sha256').update(text).digest('hex');

export function validConfiguration(raw, digest, kind) {
  try {
    if (hash(raw) !== digest) return false;
    const config = JSON.parse(raw),
      contract = REUSE_CONTRACT[kind],
      model = config.model;
    if (
      config.version !== contract.version ||
      config.codeHashes?.[contract.validatorFile] !== contract.validatorHash
    )
      return false;
    if (config.provider === 'openai-responses') {
      if (
        model.id !== REUSE_CONTRACT.openaiModel ||
        config.reasoning !== 'low' ||
        config.serviceTier !== 'default' ||
        config.providerVersion !== REUSE_CONTRACT.openaiProfile
      )
        return false;
    } else if (
      config.provider != null ||
      model.id !== REUSE_CONTRACT.legacyModel ||
      config.reasoning !== 'none' ||
      model.quantization?.name !== 'Q2_K_XL' ||
      model.config?.context_length !== 65536 ||
      model.config?.parallel !== 4
    )
      return false;
    if (kind === 'extraction')
      return (
        config.structuredOutput === true &&
        config.maxTokens === 4096 &&
        ['v7', 'v8-shared-job-prefix'].includes(config.profile) &&
        config.promptHashes?.salary === contract.salaryPromptHash &&
        config.promptHashes?.keywords === contract.keywordsPromptHash &&
        (config.profile !== 'v8-shared-job-prefix' ||
          config.prefixVersion === 'shared-job-details-v1')
      );
    return (
      config.prompt_hash === contract.promptHash &&
      config.countries_hash === contract.countriesHash
    );
  } catch {
    return false;
  }
}
export function compatibleOrigin(origin, current, kind) {
  return (
    validConfiguration(
      origin.configuration_json,
      origin.configuration_hash,
      kind,
    ) &&
    validConfiguration(
      JSON.stringify(current.configuration),
      current.configurationHash,
      kind,
    ) &&
    (origin.configuration_hash === current.configurationHash ||
      (current.configuration.provider === 'openai-responses' &&
        current.configuration.reusePolicy === REUSE_CONTRACT.policy))
  );
}
