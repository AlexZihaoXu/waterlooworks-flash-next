import type { Readable, Writable } from 'node:stream';
import {
  MAX_LINE_BYTES,
  ProtocolError,
  parseInbound,
  type Inbound,
  type JsonObject,
} from './protocol.js';

export class RpcError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = 'RpcError';
  }
}

interface Pending {
  resolve: (value: JsonObject) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
}

/**
 * Line-delimited JSON over the inherited pipes. Only this module writes to
 * stdout; nothing else in the sidecar may print. Each RPC is answered by the
 * backend with a matching `rpc_result`.
 */
export class Channel {
  #pending = new Map<number, Pending>();
  #nextId = 0;
  #buffer = Buffer.alloc(0);
  #closed = false;
  #onMessage: (message: Exclude<Inbound, { type: 'rpc_result' }>) => void;
  #onClose: (reason: string) => void;

  constructor(
    private readonly input: Readable,
    private readonly output: Writable,
    handlers: {
      onMessage: (message: Exclude<Inbound, { type: 'rpc_result' }>) => void;
      onClose: (reason: string) => void;
    },
    private readonly rpcTimeoutMs = 120_000,
  ) {
    this.#onMessage = handlers.onMessage;
    this.#onClose = handlers.onClose;
    input.on('data', (chunk: Buffer) => this.#data(chunk));
    input.on('end', () => this.close('eof'));
    input.on('error', () => this.close('input_error'));
    output.on('error', () => this.close('output_error'));
  }

  get closed(): boolean {
    return this.#closed;
  }

  #data(chunk: Buffer): void {
    if (this.#closed) return;
    this.#buffer = Buffer.concat([this.#buffer, chunk]);
    let end: number;
    while ((end = this.#buffer.indexOf(10)) !== -1) {
      const line = this.#buffer.subarray(0, end).toString('utf8');
      this.#buffer = this.#buffer.subarray(end + 1);
      if (end > MAX_LINE_BYTES) return this.close('line_too_long');
      let message: Inbound;
      try {
        message = parseInbound(line);
      } catch (error) {
        return this.close(
          error instanceof ProtocolError
            ? `protocol_${error.code}`
            : 'protocol',
        );
      }
      if (message.type === 'rpc_result') {
        const pending = this.#pending.get(message.id);
        if (!pending) return this.close('protocol_unexpected_result');
        this.#pending.delete(message.id);
        clearTimeout(pending.timer);
        if (message.ok) pending.resolve(message.result ?? {});
        else pending.reject(new RpcError(message.error ?? 'refused'));
      } else {
        this.#onMessage(message);
      }
    }
    if (this.#buffer.length > MAX_LINE_BYTES) this.close('line_too_long');
  }

  send(value: Record<string, unknown>): void {
    if (this.#closed) return;
    this.output.write(JSON.stringify(value) + '\n');
  }

  call(method: string, params: Record<string, unknown>): Promise<JsonObject> {
    if (this.#closed) return Promise.reject(new RpcError('channel_closed'));
    const id = this.#nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id);
        reject(new RpcError('rpc_timeout'));
      }, this.rpcTimeoutMs);
      this.#pending.set(id, { resolve, reject, timer });
      this.send({ type: 'rpc', id, method, params });
    });
  }

  close(reason: string): void {
    if (this.#closed) return;
    this.#closed = true;
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new RpcError('channel_closed'));
    }
    this.#pending.clear();
    this.#onClose(reason);
  }
}
