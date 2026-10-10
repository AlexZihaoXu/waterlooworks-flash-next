/**
 * Strict JSON-lines protocol between the backend (authoritative) and this
 * disposable sidecar. Every inbound object must have exactly the expected keys;
 * failures raise ProtocolError with a fixed code and never echo input values.
 */

export const PROTOCOL_VERSION = 1;
export const MAX_LINE_BYTES = 4 * 1024 * 1024;

export const ACCESS_MODES = [
  'openai_api_key',
  'chatgpt_signin',
  'codex_device',
  'custom',
] as const;
export type AccessMode = (typeof ACCESS_MODES)[number];

export const ITEM_KINDS = ['extraction', 'country'] as const;
export type ItemKind = (typeof ITEM_KINDS)[number];

export class ProtocolError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = 'ProtocolError';
  }
}

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type JsonObject = { [key: string]: Json };

export interface CustomEndpoint {
  baseUrl: string;
  host: string;
  pinnedAddress: string;
  contextWindow: number;
  reasoningField: string | null;
  disableWith: Record<string, string | boolean | number | null> | null;
}

export interface StartConfig {
  mode: AccessMode;
  modelId: string;
  reasoning: string | null;
  concurrency: number;
  maxTurns: number;
  maxToolCalls: number;
  maxOutputTokens: number;
  requestTimeoutMs: number;
  itemTimeoutMs: number;
  custom: CustomEndpoint | null;
}

export interface Section {
  name: string;
  text: string;
}

export interface ItemMessage {
  itemId: string;
  kind: ItemKind;
  system: string;
  user: string;
  sections: Section[];
  submitSchema: JsonObject;
}

export type Inbound =
  | { type: 'start'; config: StartConfig }
  | { type: 'item'; item: ItemMessage }
  | { type: 'end' }
  | {
      type: 'rpc_result';
      id: number;
      ok: boolean;
      result: JsonObject | null;
      error: string | null;
    };

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function exactKeys(
  value: unknown,
  required: readonly string[],
  optional: readonly string[] = [],
): Record<string, unknown> {
  if (!isObject(value)) throw new ProtocolError('shape');
  const allowed = new Set([...required, ...optional]);
  for (const key of Object.keys(value))
    if (!allowed.has(key)) throw new ProtocolError('unknown_field');
  for (const key of required)
    if (!Object.hasOwn(value, key)) throw new ProtocolError('missing_field');
  return value;
}

function boundedString(
  value: unknown,
  max: number,
  pattern?: RegExp,
  min = 1,
): string {
  if (
    typeof value !== 'string' ||
    value.length < min ||
    value.length > max ||
    (pattern !== undefined && !pattern.test(value))
  )
    throw new ProtocolError('invalid_string');
  return value;
}

function boundedInt(value: unknown, min: number, max: number): number {
  if (
    !Number.isSafeInteger(value) ||
    (value as number) < min ||
    (value as number) > max
  )
    throw new ProtocolError('invalid_integer');
  return value as number;
}

function oneOf<T extends string>(value: unknown, options: readonly T[]): T {
  if (typeof value !== 'string' || !options.includes(value as T))
    throw new ProtocolError('invalid_enum');
  return value as T;
}

const MODEL_ID = /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$/;
const EFFORT = /^[a-z][a-z0-9_-]{0,23}$/;
const FIELD = /^[a-z][a-z0-9_]{0,39}$/;
const ITEM_ID = /^[a-z0-9][a-z0-9-]{0,79}$/;
const RESERVED_FIELDS = new Set([
  'model',
  'messages',
  'tools',
  'tool_choice',
  'stream',
  'stream_options',
  'max_tokens',
  'max_completion_tokens',
  'response_format',
  'store',
]);

function parseCustom(value: unknown): CustomEndpoint {
  const raw = exactKeys(value, [
    'base_url',
    'host',
    'pinned_address',
    'context_window',
    'reasoning_field',
    'disable_with',
  ]);
  const baseUrl = boundedString(raw.base_url, 2048);
  let url: URL;
  try {
    url = new URL(baseUrl);
  } catch {
    throw new ProtocolError('invalid_url');
  }
  if (
    !['http:', 'https:'].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    throw new ProtocolError('invalid_url');
  const host = boundedString(raw.host, 253);
  if (url.hostname.replace(/^\[|\]$/g, '') !== host)
    throw new ProtocolError('invalid_url');
  const pinnedAddress = boundedString(
    raw.pinned_address,
    45,
    /^[0-9a-fA-F:.]+$/,
  );
  const reasoningField =
    raw.reasoning_field === null
      ? null
      : boundedString(raw.reasoning_field, 40, FIELD);
  if (reasoningField !== null && RESERVED_FIELDS.has(reasoningField))
    throw new ProtocolError('invalid_reasoning_field');
  let disableWith: CustomEndpoint['disableWith'] = null;
  if (raw.disable_with !== null) {
    if (!isObject(raw.disable_with)) throw new ProtocolError('shape');
    const entries = Object.entries(raw.disable_with);
    if (entries.length > 4) throw new ProtocolError('invalid_disable_with');
    disableWith = {};
    for (const [key, item] of entries) {
      boundedString(key, 40, FIELD);
      if (RESERVED_FIELDS.has(key))
        throw new ProtocolError('invalid_disable_with');
      if (
        item !== null &&
        typeof item !== 'boolean' &&
        !(typeof item === 'number' && Number.isFinite(item)) &&
        !(typeof item === 'string' && item.length <= 40)
      )
        throw new ProtocolError('invalid_disable_with');
      disableWith[key] = item as string | boolean | number | null;
    }
  }
  return {
    baseUrl: baseUrl.replace(/\/+$/, ''),
    host,
    pinnedAddress,
    contextWindow: boundedInt(raw.context_window, 1024, 10_000_000),
    reasoningField,
    disableWith,
  };
}

export function parseStart(value: unknown): StartConfig {
  const raw = exactKeys(value, [
    'type',
    'protocol',
    'mode',
    'model_id',
    'reasoning',
    'concurrency',
    'max_turns',
    'max_tool_calls',
    'max_output_tokens',
    'request_timeout_ms',
    'item_timeout_ms',
    'custom',
  ]);
  if (raw.type !== 'start' || raw.protocol !== PROTOCOL_VERSION)
    throw new ProtocolError('protocol');
  const mode = oneOf(raw.mode, ACCESS_MODES);
  const custom = raw.custom === null ? null : parseCustom(raw.custom);
  if ((mode === 'custom') !== (custom !== null))
    throw new ProtocolError('custom_mismatch');
  return {
    mode,
    modelId: boundedString(raw.model_id, 200, MODEL_ID),
    reasoning:
      raw.reasoning === null ? null : boundedString(raw.reasoning, 24, EFFORT),
    concurrency: boundedInt(raw.concurrency, 1, 8),
    maxTurns: boundedInt(raw.max_turns, 1, 24),
    maxToolCalls: boundedInt(raw.max_tool_calls, 1, 48),
    maxOutputTokens: boundedInt(raw.max_output_tokens, 64, 65_536),
    requestTimeoutMs: boundedInt(raw.request_timeout_ms, 1_000, 600_000),
    itemTimeoutMs: boundedInt(raw.item_timeout_ms, 1_000, 3_600_000),
    custom,
  };
}

function parseSchema(value: unknown): JsonObject {
  if (!isObject(value) || value.type !== 'object')
    throw new ProtocolError('invalid_schema');
  if (JSON.stringify(value).length > 256 * 1024)
    throw new ProtocolError('invalid_schema');
  return value as JsonObject;
}

export function parseItem(value: unknown): ItemMessage {
  const raw = exactKeys(value, [
    'type',
    'item_id',
    'kind',
    'system',
    'user',
    'sections',
    'submit_schema',
  ]);
  if (!Array.isArray(raw.sections) || raw.sections.length > 64)
    throw new ProtocolError('invalid_sections');
  const names = new Set<string>();
  const sections = raw.sections.map((entry) => {
    const section = exactKeys(entry, ['name', 'text']);
    const name = boundedString(section.name, 120);
    if (names.has(name)) throw new ProtocolError('duplicate_section');
    names.add(name);
    return { name, text: boundedString(section.text, 400_000, undefined, 0) };
  });
  return {
    itemId: boundedString(raw.item_id, 80, ITEM_ID),
    kind: oneOf(raw.kind, ITEM_KINDS),
    system: boundedString(raw.system, 20_000),
    user: boundedString(raw.user, 200_000),
    sections,
    submitSchema: parseSchema(raw.submit_schema),
  };
}

export function parseInbound(line: string): Inbound {
  let value: unknown;
  try {
    value = JSON.parse(line);
  } catch {
    throw new ProtocolError('json');
  }
  if (!isObject(value) || typeof value.type !== 'string')
    throw new ProtocolError('shape');
  switch (value.type) {
    case 'start':
      return { type: 'start', config: parseStart(value) };
    case 'item':
      return { type: 'item', item: parseItem(value) };
    case 'end':
      exactKeys(value, ['type']);
      return { type: 'end' };
    case 'rpc_result': {
      const raw = exactKeys(value, ['type', 'id', 'ok'], ['result', 'error']);
      if (typeof raw.ok !== 'boolean') throw new ProtocolError('shape');
      const result = raw.result ?? null;
      if (result !== null && !isObject(result))
        throw new ProtocolError('shape');
      const error =
        raw.error === undefined || raw.error === null
          ? null
          : boundedString(raw.error, 64, /^[a-z_]+$/);
      return {
        type: 'rpc_result',
        id: boundedInt(raw.id, 0, 2 ** 31 - 1),
        ok: raw.ok,
        result: result as JsonObject | null,
        error,
      };
    }
    default:
      throw new ProtocolError('unknown_type');
  }
}
