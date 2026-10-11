import { randomUUID } from 'node:crypto';
import { Agent, type ThinkingLevel } from '@earendil-works/pi-agent-core';
import {
  createAssistantMessageEventStream,
  type Api,
  type AssistantMessage,
  type AssistantMessageEventStream,
  type Model,
  type SimpleStreamOptions,
  type ThinkingLevel as PiThinkingLevel,
} from '@earendil-works/pi-ai';
import { RpcError } from './channel.js';
import type { RpcCaller } from './credentials.js';
import type { ItemMessage, JsonObject, StartConfig } from './protocol.js';
import type { Runtime } from './providers.js';
import { buildTools, type SubmissionState } from './tools.js';

export type FailureCategory =
  | 'auth'
  | 'budget_stopped'
  | 'connection'
  | 'evidence_rejected'
  | 'http_client'
  | 'http_server'
  | 'internal'
  | 'ledger'
  | 'max_tool_calls'
  | 'max_turns'
  | 'no_submission'
  | 'rate_limited'
  | 'timeout';

export interface ItemOutcome {
  status: 'submitted' | 'failed';
  payload: JsonObject | null;
  category: FailureCategory | null;
  turns: number;
  toolCalls: number;
}

const ZERO_USAGE = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheWrite: 0,
  totalTokens: 0,
  cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
};

function errorStream(
  model: Model<Api>,
  reason: string,
): AssistantMessageEventStream {
  const stream = createAssistantMessageEventStream();
  const message: AssistantMessage = {
    role: 'assistant',
    content: [],
    api: model.api,
    provider: model.provider,
    model: model.id,
    usage: { ...ZERO_USAGE, cost: { ...ZERO_USAGE.cost } },
    stopReason: 'error',
    errorMessage: reason,
    timestamp: Date.now(),
  };
  queueMicrotask(() => {
    stream.push({ type: 'error', reason: 'error', error: message });
    stream.end(message);
  });
  return stream;
}

function counter(value: unknown): number | null {
  return Number.isSafeInteger(value) && (value as number) >= 0
    ? (value as number)
    : null;
}

export function categorizeStatus(
  status: number | null,
  timedOut: boolean,
): FailureCategory {
  if (status === 401 || status === 403) return 'auth';
  if (status === 429) return 'rate_limited';
  if (status !== null && status >= 500) return 'http_server';
  if (status !== null && status >= 400) return 'http_client';
  return timedOut ? 'timeout' : 'connection';
}

export function submitToolName(item: ItemMessage): string {
  return item.kind === 'extraction' ? 'submit_extraction' : 'submit_country';
}

/** Named tool_choice in the shape each OpenAI wire API expects. */
export function forcedToolChoice(api: string, name: string): object {
  return api === 'openai-completions'
    ? { type: 'function', function: { name } }
    : { type: 'function', name };
}

export interface RunnerContext {
  config: StartConfig;
  runtime: Runtime;
  rpc: RpcCaller;
  fetchImpl: typeof fetch;
}

/** Map the configured effort to pi's standard levels (custom uses onPayload). */
function thinkingLevel(config: StartConfig): ThinkingLevel {
  if (config.mode === 'custom' || config.reasoning === null) return 'off';
  return config.reasoning as ThinkingLevel;
}

function customPayload(config: StartConfig) {
  const custom = config.custom;
  if (!custom || custom.reasoningField === null || config.reasoning === null)
    return undefined;
  return (payload: unknown) => {
    if (typeof payload !== 'object' || payload === null) return undefined;
    const body = { ...(payload as Record<string, unknown>) };
    if (config.reasoning === 'off') {
      if (custom.disableWith) Object.assign(body, custom.disableWith);
      return body;
    }
    body[custom.reasoningField!] = config.reasoning;
    return body;
  };
}

/**
 * One fresh agent per posting: new transcript, only this item's tools, no
 * memory shared with other postings. The backend reserves budget before every
 * provider request and records its usage after the response ends.
 */
export async function runItem(
  context: RunnerContext,
  item: ItemMessage,
  externalSignal?: AbortSignal,
): Promise<ItemOutcome> {
  const { config, runtime, rpc } = context;
  const submission: SubmissionState = {
    payload: null,
    attempts: 0,
    rejected: false,
  };
  let turns = 0;
  let toolCalls = 0;
  let failure: FailureCategory | null = null;
  let lastStatus: number | null = null;
  let lastTimedOut = false;
  let nudged = false;
  let pendingFinalize: Promise<void> = Promise.resolve();
  const itemAbort = new AbortController();
  const itemTimer = setTimeout(() => {
    failure ??= 'timeout';
    itemAbort.abort();
  }, config.itemTimeoutMs);
  const onExternalAbort = () => itemAbort.abort();
  externalSignal?.addEventListener('abort', onExternalAbort, { once: true });
  const injectPayload = customPayload(config);
  const sessionId = randomUUID();

  const streamFn = async (
    model: Model<Api>,
    transcript: Parameters<Runtime['models']['streamSimple']>[1],
    options?: SimpleStreamOptions,
  ): Promise<AssistantMessageEventStream> => {
    await pendingFinalize;
    if (failure !== null || itemAbort.signal.aborted)
      return errorStream(model, 'stopped');
    if (turns >= config.maxTurns) {
      failure = 'max_turns';
      return errorStream(model, 'turn limit');
    }
    turns += 1;
    let attempt: string;
    try {
      const reserved = await rpc.call('ledger.reserve', {
        item_id: item.itemId,
        turn: turns,
      });
      if (typeof reserved.attempt !== 'string') throw new RpcError('ledger');
      attempt = reserved.attempt;
    } catch (error) {
      failure =
        error instanceof RpcError && error.code === 'budget_stopped'
          ? 'budget_stopped'
          : 'ledger';
      return errorStream(model, 'budget');
    }
    let status: number | null = null;
    const timeout = AbortSignal.timeout(config.requestTimeoutMs);
    const signals = [timeout, itemAbort.signal];
    if (options?.signal) signals.push(options.signal);
    const request: SimpleStreamOptions = {
      ...options,
      signal: AbortSignal.any(signals),
      // Record every HTTP status this request sees, including SDK-level errors
      // that never reach onResponse.
      fetch: async (input, init) => {
        const response = await context.fetchImpl(input, init);
        status = response.status;
        return response;
      },
      maxRetries: 0,
      timeoutMs: config.requestTimeoutMs,
      maxTokens: config.maxOutputTokens,
      env: {},
      transport: 'sse',
      sessionId,
      onResponse: (response) => {
        status = response.status;
      },
    };
    // Final allowed turn: force the submit tool so the posting cannot end in
    // prose or another read (tool_choice by name, per wire API).
    const forceSubmit = turns >= config.maxTurns && turns > 1;
    if (injectPayload || forceSubmit)
      request.onPayload = (payload) => {
        let body = injectPayload
          ? (injectPayload(payload) ?? payload)
          : payload;
        if (forceSubmit && typeof body === 'object' && body !== null)
          body = {
            ...(body as Record<string, unknown>),
            tool_choice: forcedToolChoice(model.api, submitToolName(item)),
          };
        return body;
      };
    if (config.mode !== 'custom' && config.reasoning !== null)
      request.reasoning = config.reasoning as PiThinkingLevel;
    let stream: AssistantMessageEventStream;
    try {
      stream = await runtime.models.streamSimple(model, transcript, request);
    } catch {
      stream = errorStream(model, 'setup');
    }
    pendingFinalize = stream
      .result()
      .then(async (message) => {
        lastStatus = status;
        lastTimedOut = timeout.aborted;
        const usage = message.usage ?? ZERO_USAGE;
        await rpc.call('ledger.finalize', {
          attempt,
          outcome:
            message.stopReason === 'error'
              ? 'error'
              : message.stopReason === 'aborted'
                ? 'aborted'
                : 'completed',
          http_status: status,
          model:
            typeof message.responseModel === 'string'
              ? message.responseModel
              : typeof message.model === 'string'
                ? message.model
                : null,
          usage: {
            input: counter(usage.input),
            output: counter(usage.output),
            cache_read: counter(usage.cacheRead),
            cache_write: counter(usage.cacheWrite),
            reasoning: counter((usage as { reasoning?: unknown }).reasoning),
          },
          // pi-ai catalog estimate; the backend decides whether it applies.
          cost_usd:
            typeof usage.cost?.total === 'number' &&
            Number.isFinite(usage.cost.total) &&
            usage.cost.total >= 0
              ? usage.cost.total
              : null,
        });
      })
      .catch(() => {
        failure ??= 'ledger';
      });
    return stream;
  };

  const agent = new Agent({
    initialState: {
      systemPrompt: item.system,
      model: runtime.model,
      tools: buildTools(item, rpc, submission),
      thinkingLevel: thinkingLevel(config),
    },
    streamFn,
    sessionId,
    toolExecution: 'sequential',
    beforeToolCall: async () => {
      toolCalls += 1;
      if (toolCalls > config.maxToolCalls) {
        failure ??= 'max_tool_calls';
        return {
          block: true,
          reason: 'Tool call limit reached.',
          terminate: true,
        };
      }
      return undefined;
    },
    finishTurn: async ({ message }) => {
      if (submission.payload !== null || submission.rejected)
        return { action: 'end' };
      if (message.stopReason === 'error' || message.stopReason === 'aborted')
        return undefined;
      if (failure !== null) return { action: 'end' };
      if (turns >= config.maxTurns) {
        failure = 'max_turns';
        return { action: 'end' };
      }
      if (message.stopReason === 'stop' && !nudged) {
        nudged = true;
        agent.followUp({
          role: 'user',
          content: `Call ${item.kind === 'extraction' ? 'submit_extraction' : 'submit_country'} now with your final answer.`,
          timestamp: Date.now(),
        });
      }
      return undefined;
    },
  });
  itemAbort.signal.addEventListener('abort', () => agent.abort(), {
    once: true,
  });

  try {
    await agent.prompt(item.user);
  } catch {
    failure ??= 'internal';
  } finally {
    clearTimeout(itemTimer);
    externalSignal?.removeEventListener('abort', onExternalAbort);
  }
  await pendingFinalize;

  if (submission.payload !== null)
    return {
      status: 'submitted',
      payload: submission.payload,
      category: null,
      turns,
      toolCalls,
    };
  let category: FailureCategory | null = failure;
  if (category === null) {
    const last = [...agent.state.messages]
      .reverse()
      .find((message) => message.role === 'assistant') as
      AssistantMessage | undefined;
    if (submission.rejected) category = 'evidence_rejected';
    else if (last?.stopReason === 'error' || last?.stopReason === 'aborted')
      category = categorizeStatus(lastStatus, lastTimedOut);
    else category = 'no_submission';
  }
  return { status: 'failed', payload: null, category, turns, toolCalls };
}
