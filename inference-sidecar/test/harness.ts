import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = fileURLToPath(new URL('../../', import.meta.url));
export const MAIN = path.join(ROOT, 'dist', 'src', 'main.js');

export type RpcHandler = (
  method: string,
  params: Record<string, unknown>,
) =>
  { ok: true; result: Record<string, unknown> } | { ok: false; error: string };

export interface SessionResult {
  code: number | null;
  messages: Record<string, unknown>[];
  rpcs: { method: string; params: Record<string, unknown> }[];
  stdout: string;
  stderr: string;
  home: string;
  homeEntries: string[];
}

export function startMessage(
  baseUrl: string,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    type: 'start',
    protocol: 1,
    mode: 'custom',
    model_id: 'fake-model',
    reasoning: null,
    concurrency: 1,
    max_turns: 6,
    max_tool_calls: 12,
    max_output_tokens: 1024,
    request_timeout_ms: 10_000,
    item_timeout_ms: 30_000,
    custom: {
      base_url: baseUrl,
      host: '127.0.0.1',
      pinned_address: '127.0.0.1',
      context_window: 32768,
      reasoning_field: null,
      disable_with: null,
    },
    ...overrides,
  };
}

export const EXTRACTION_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['base_rates', 'skills'],
  properties: {
    base_rates: { type: 'array', maxItems: 20, items: { type: 'object' } },
    skills: { type: 'array', maxItems: 100, items: { type: 'object' } },
  },
};

export function itemMessage(
  itemId: string,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    type: 'item',
    item_id: itemId,
    kind: 'extraction',
    system: 'Extract source-grounded pay and skills. Use tools.',
    user: 'Sections: [{"name":"Compensation and Benefits:","chars":19}]',
    sections: [
      { name: 'Compensation and Benefits:', text: '$25 CAD per hour' },
      { name: 'Required Skills:', text: 'Python is required.' },
    ],
    submit_schema: EXTRACTION_SCHEMA,
    ...overrides,
  };
}

/**
 * Spawn the built sidecar exactly like the backend does: Node permission
 * model limited to the package directory, an empty private HOME and only the
 * variables listed here. Answers RPCs with `handler`.
 */
export async function runSession(
  lines:
    | Record<string, unknown>[]
    | ((send: (v: object) => void, msg: Record<string, unknown>) => void),
  handler: RpcHandler,
  options: {
    endAfterItems?: boolean;
    timeoutMs?: number;
    extraArgs?: string[];
  } = {},
): Promise<SessionResult> {
  const home = fs.mkdtempSync(
    path.join(os.tmpdir(), `sidecar-home-${randomBytes(4).toString('hex')}-`),
  );
  const child = spawn(
    process.execPath,
    [
      '--permission',
      `--allow-fs-read=${ROOT}`,
      ...(options.extraArgs ?? []),
      MAIN,
      'run',
    ],
    {
      cwd: home,
      env: {
        HOME: home,
        XDG_CONFIG_HOME: home,
        XDG_CACHE_HOME: home,
        XDG_DATA_HOME: home,
        TZ: 'UTC',
      },
      stdio: ['pipe', 'pipe', 'pipe'],
    },
  );
  const result: SessionResult = {
    code: null,
    messages: [],
    rpcs: [],
    stdout: '',
    stderr: '',
    home,
    homeEntries: [],
  };
  const send = (value: object) =>
    child.stdin.write(JSON.stringify(value) + '\n');
  let buffer = '';
  child.stdout.on('data', (chunk: Buffer) => {
    result.stdout += chunk.toString('utf8');
    buffer += chunk.toString('utf8');
    let end: number;
    while ((end = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, end);
      buffer = buffer.slice(end + 1);
      if (!line) continue;
      const message = JSON.parse(line) as Record<string, unknown>;
      if (message.type === 'rpc') {
        const params = message.params as Record<string, unknown>;
        result.rpcs.push({ method: message.method as string, params });
        const answer = handler(message.method as string, params);
        send({ type: 'rpc_result', id: message.id, ...answer });
        continue;
      }
      result.messages.push(message);
      if (typeof lines === 'function') lines(send, message);
    }
  });
  child.stderr.on(
    'data',
    (chunk: Buffer) => (result.stderr += chunk.toString('utf8')),
  );
  if (Array.isArray(lines)) for (const line of lines) send(line);
  const timer = setTimeout(
    () => child.kill('SIGKILL'),
    options.timeoutMs ?? 30_000,
  );
  result.code = await new Promise((resolve) =>
    child.on('exit', (code) => resolve(code)),
  );
  clearTimeout(timer);
  result.homeEntries = fs.readdirSync(home);
  fs.rmSync(home, { recursive: true, force: true });
  return result;
}

/** Default backend: unlimited budget, accepts any submission. */
export function backend(
  overrides: Partial<Record<string, RpcHandler>> = {},
  credential: Record<string, unknown> | null = {
    type: 'api_key',
    key: 'test-token',
  },
): RpcHandler {
  let attempts = 0;
  return (method, params) => {
    const override = overrides[method];
    if (override) return override(method, params);
    switch (method) {
      case 'credential.read':
        return { ok: true, result: { credential } };
      case 'ledger.reserve':
        attempts += 1;
        return { ok: true, result: { attempt: `attempt-${attempts}` } };
      case 'ledger.finalize':
        return { ok: true, result: {} };
      case 'submission.check':
        return { ok: true, result: { accepted: true } };
      case 'tool.check_pay':
        return { ok: true, result: { state: 'recognized' } };
      default:
        return { ok: false, error: 'refused' };
    }
  };
}
