import {
  createModels,
  createProvider,
  getSupportedThinkingLevels,
  type Api,
  type CredentialStore,
  type Model,
  type MutableModels,
} from '@earendil-works/pi-ai';
import { openAICompletionsApi } from '@earendil-works/pi-ai/api/openai-completions.lazy';
import { openaiProvider } from '@earendil-works/pi-ai/providers/openai';
import { openaiCodexProvider } from '@earendil-works/pi-ai/providers/openai-codex';
import type { AccessMode, StartConfig } from './protocol.js';

export const OPENAI_API_ORIGIN = 'https://api.openai.com';
export const OPENAI_AUTH_ORIGIN = 'https://auth.openai.com';
export const CODEX_ORIGIN = 'https://chatgpt.com';

export class ProviderSetupError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = 'ProviderSetupError';
  }
}

export function providerIdFor(mode: AccessMode): string {
  return mode === 'codex_device'
    ? 'openai-codex'
    : mode === 'custom'
      ? 'custom'
      : 'openai';
}

/** Exact origins (and DNS names) a mode may contact; nothing else resolves. */
export function networkPolicy(config: StartConfig): {
  origins: Set<string>;
  hosts: Set<string>;
  pinned: Map<string, string>;
} {
  switch (config.mode) {
    case 'openai_api_key':
      return {
        origins: new Set([OPENAI_API_ORIGIN]),
        hosts: new Set(['api.openai.com']),
        pinned: new Map(),
      };
    case 'chatgpt_signin':
      return {
        origins: new Set([OPENAI_API_ORIGIN, OPENAI_AUTH_ORIGIN]),
        hosts: new Set(['api.openai.com', 'auth.openai.com']),
        pinned: new Map(),
      };
    case 'codex_device':
      return {
        origins: new Set([CODEX_ORIGIN, OPENAI_AUTH_ORIGIN]),
        hosts: new Set(['chatgpt.com', 'auth.openai.com']),
        pinned: new Map(),
      };
    case 'custom': {
      const custom = config.custom!;
      const origin = new URL(custom.baseUrl).origin;
      return {
        origins: new Set([origin]),
        hosts: new Set(),
        pinned: new Map([[custom.host.toLowerCase(), custom.pinnedAddress]]),
      };
    }
  }
}

function customModel(config: StartConfig): Model<'openai-completions'> {
  const custom = config.custom!;
  return {
    id: config.modelId,
    name: config.modelId,
    api: 'openai-completions',
    provider: 'custom',
    baseUrl: custom.baseUrl,
    // Reasoning parameters are injected verbatim from the server's own
    // metadata (see onPayload), never guessed by pi-ai.
    reasoning: false,
    input: ['text'],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: custom.contextWindow,
    maxTokens: config.maxOutputTokens,
    compat: {
      supportsStore: false,
      supportsDeveloperRole: false,
      supportsReasoningEffort: false,
      supportsUsageInStreaming: true,
      supportsStrictMode: false,
      maxTokensField: 'max_tokens',
      sendSessionAffinityHeaders: false,
    },
  };
}

export interface Runtime {
  models: MutableModels;
  model: Model<Api>;
  providerId: string;
}

export function createRuntime(
  config: StartConfig,
  credentials: CredentialStore,
): Runtime {
  const models = createModels({ credentials });
  const providerId = providerIdFor(config.mode);
  if (config.mode === 'custom') {
    const model = customModel(config);
    models.setProvider(
      createProvider({
        id: 'custom',
        name: 'Custom OpenAI-compatible server',
        baseUrl: config.custom!.baseUrl,
        auth: {
          apiKey: {
            name: 'Custom server token',
            resolve: async ({ credential }) => ({
              // Keyless local servers still need a syntactically present key.
              auth: { apiKey: credential?.key || 'unused' },
              source: 'stored credential',
            }),
          },
        },
        models: [model],
        api: openAICompletionsApi(),
      }),
    );
    return { models, model, providerId };
  }
  models.setProvider(
    config.mode === 'codex_device' ? openaiCodexProvider() : openaiProvider(),
  );
  const model = models.getModel(providerId, config.modelId);
  if (!model) throw new ProviderSetupError('model_unavailable');
  if (
    config.reasoning !== null &&
    !getSupportedThinkingLevels(model).includes(
      config.reasoning as ReturnType<typeof getSupportedThinkingLevels>[number],
    )
  )
    throw new ProviderSetupError('reasoning_unsupported');
  return { models, model, providerId };
}

export interface CatalogEntry {
  id: string;
  name: string;
  context_window: number;
  max_output_tokens: number;
  reasoning: boolean;
  efforts: string[];
}

/** Static pi-ai catalog for the subscription/API providers; no network. */
export function catalog(provider: 'openai' | 'openai-codex'): CatalogEntry[] {
  const models = createModels();
  models.setProvider(
    provider === 'openai' ? openaiProvider() : openaiCodexProvider(),
  );
  return models.getModels(provider).map((model) => ({
    id: model.id,
    name: model.name,
    context_window: model.contextWindow,
    max_output_tokens: model.maxTokens,
    reasoning: model.reasoning,
    efforts: getSupportedThinkingLevels(model),
  }));
}
