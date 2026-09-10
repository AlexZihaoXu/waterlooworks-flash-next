import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

export const OPENAI_MODEL = 'gpt-5.6-luna';
export const OPENAI_PROFILE = 'openai-responses-low-strict-v1';
const root = fileURLToPath(new URL('../', import.meta.url));
const maximumLineBytes = 4 * 1024 * 1024;

export function encodeBridgeMessage(message) {
  const line = Buffer.from(JSON.stringify(message) + '\n');
  if (line.length > 2 * 1024 * 1024)
    throw new Error('OpenAI bridge input exceeds its bound');
  return line;
}

export class OpenAIBridge {
  #child;
  #pending = new Map();
  #buffer = Buffer.alloc(0);
  #nextId = 0;
  #closed = false;
  #lifetime;
  constructor() {
    // One process per CLI RUN, not per slice. Only the backend decrypts keys.
    const environment =
      process.env.UV_PROJECT_ENVIRONMENT ?? path.join(root, 'backend', '.venv');
    const python = path.resolve(
      environment,
      process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
    );
    this.#child = spawn(
      python,
      ['-m', 'waterlooworks_flash_next.openai_bridge'],
      {
        cwd: root,
        shell: false,
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true,
      },
    );
    this.#child.stderr.on('data', () => {}); // Never forward private diagnostics.
    this.#child.on('error', () => this.#fail());
    this.#child.on('exit', () => this.#fail());
    this.#child.stdin.on('error', () => this.#fail());
    this.#child.stdout.on('data', (chunk) => {
      this.#buffer = Buffer.concat([this.#buffer, chunk]);
      if (this.#buffer.length > maximumLineBytes) return this.#fail();
      let end;
      while ((end = this.#buffer.indexOf(10)) !== -1) {
        const line = this.#buffer.subarray(0, end);
        this.#buffer = this.#buffer.subarray(end + 1);
        try {
          const value = JSON.parse(line.toString('utf8'));
          const pending = this.#pending.get(value.id);
          if (!pending || typeof value.ok !== 'boolean') return this.#fail();
          this.#pending.delete(value.id);
          clearTimeout(pending.timer);
          if (value.ok) pending.resolve(value);
          else
            pending.reject(
              new Error(
                value.error === 'budget_stopped'
                  ? 'OpenAI budget stopped; review unknown usage and persisted limits'
                  : 'OpenAI bridge unavailable; checkpoints retained',
              ),
            );
        } catch {
          return this.#fail();
        }
      }
    });
    this.#lifetime = setTimeout(() => this.#fail(), 24 * 3600000);
  }
  #fail() {
    if (this.#closed) return;
    this.#closed = true;
    clearTimeout(this.#lifetime);
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(
        new Error(
          'OpenAI bridge stopped; usage may be unknown; no automatic retry',
        ),
      );
    }
    this.#pending.clear();
    this.#child.stdin.destroy();
    this.#child.kill();
  }
  request(body) {
    if (this.#closed || this.#pending.size >= 4)
      return Promise.reject(new Error('OpenAI bridge unavailable or busy'));
    const id = this.#nextId++;
    const line = encodeBridgeMessage({ id, ...body });
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => this.#fail(), 150000);
      this.#pending.set(id, { resolve, reject, timer });
      this.#child.stdin.write(line);
    });
  }
  async initialize(database) {
    await this.request({ op: 'initialize', database, load_environment: true });
  }
  async bind(runId, owner, kind) {
    await this.request({ op: 'bind', run_id: runId, owner, kind });
  }
  async complete(
    operationHash,
    messages,
    name,
    schema,
    maxOutputTokens = 4096,
  ) {
    const result = await this.request({
      op: 'complete',
      operation_hash: operationHash,
      messages,
      name,
      schema,
      max_output_tokens: maxOutputTokens,
    });
    if (typeof result.text !== 'string') {
      this.#fail();
      throw new Error('OpenAI bridge output is invalid');
    }
    return result.text;
  }
  async close() {
    if (this.#closed) return;
    this.#child.stdin.end();
    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.#fail();
        resolve();
      }, 150000);
      this.#child.once('exit', () => {
        clearTimeout(timer);
        resolve();
      });
    });
  }
}
