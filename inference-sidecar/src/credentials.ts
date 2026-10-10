import type {
  AuthOperationOptions,
  Credential,
  CredentialInfo,
  CredentialStore,
} from '@earendil-works/pi-ai';
import { RpcError } from './channel.js';

export interface RpcCaller {
  call(
    method: string,
    params: Record<string, unknown>,
  ): Promise<Record<string, unknown>>;
}

const PROVIDER_ID = /^[a-z][a-z0-9-]{0,39}$/;

/** Accept only the two credential shapes pi-ai defines; reject everything else. */
export function parseCredential(value: unknown): Credential | undefined {
  if (value === null || value === undefined) return undefined;
  if (typeof value !== 'object' || Array.isArray(value))
    throw new RpcError('invalid_credential');
  const raw = value as Record<string, unknown>;
  if (raw.type === 'api_key') {
    if (
      Object.keys(raw).some((key) => !['type', 'key'].includes(key)) ||
      (raw.key !== undefined && typeof raw.key !== 'string')
    )
      throw new RpcError('invalid_credential');
    return raw.key === undefined
      ? { type: 'api_key' }
      : { type: 'api_key', key: raw.key as string };
  }
  if (
    raw.type === 'oauth' &&
    typeof raw.access === 'string' &&
    typeof raw.refresh === 'string' &&
    typeof raw.expires === 'number' &&
    Number.isFinite(raw.expires)
  )
    return raw as Credential;
  throw new RpcError('invalid_credential');
}

/**
 * pi-ai CredentialStore backed by the backend. Secrets are encrypted at rest by
 * the backend and only ever held in this process' memory. `modify` locks on
 * the backend, runs the (possibly refreshing) function here, then commits the
 * rotated credential; the backend persists it before acknowledging, so a
 * rotated refresh token is never lost.
 */
export class RpcCredentialStore implements CredentialStore {
  #chains = new Map<string, Promise<unknown>>();

  constructor(private readonly rpc: RpcCaller) {}

  #check(providerId: string): void {
    if (!PROVIDER_ID.test(providerId)) throw new RpcError('invalid_provider');
  }

  async read(
    providerId: string,
    _options?: AuthOperationOptions,
  ): Promise<Credential | undefined> {
    this.#check(providerId);
    const result = await this.rpc.call('credential.read', {
      provider_id: providerId,
    });
    return parseCredential(result.credential);
  }

  async list(
    _options?: AuthOperationOptions,
  ): Promise<readonly CredentialInfo[]> {
    const result = await this.rpc.call('credential.list', {});
    const entries = result.credentials;
    if (!Array.isArray(entries)) throw new RpcError('invalid_credential');
    return entries.map((entry) => {
      if (
        typeof entry !== 'object' ||
        entry === null ||
        typeof entry.provider_id !== 'string' ||
        !['api_key', 'oauth'].includes(entry.type)
      )
        throw new RpcError('invalid_credential');
      return { providerId: entry.provider_id, type: entry.type };
    });
  }

  modify(
    providerId: string,
    fn: (current: Credential | undefined) => Promise<Credential | undefined>,
    _options?: AuthOperationOptions,
  ): Promise<Credential | undefined> {
    this.#check(providerId);
    const previous = this.#chains.get(providerId) ?? Promise.resolve();
    const run = previous
      .catch(() => undefined)
      .then(async () => {
        const locked = await this.rpc.call('credential.lock', {
          provider_id: providerId,
        });
        const lock = locked.lock;
        if (typeof lock !== 'string' || !/^[0-9a-f]{32}$/.test(lock))
          throw new RpcError('invalid_lock');
        let next: Credential | undefined;
        try {
          const current = parseCredential(locked.credential);
          next = await fn(current);
          if (next === undefined) {
            await this.rpc.call('credential.release', { lock });
            return current;
          }
          parseCredential(next);
        } catch (error) {
          await this.rpc
            .call('credential.release', { lock })
            .catch(() => undefined);
          throw error;
        }
        await this.rpc.call('credential.commit', { lock, credential: next });
        return next;
      });
    this.#chains.set(providerId, run);
    return run;
  }

  async delete(
    providerId: string,
    _options?: AuthOperationOptions,
  ): Promise<void> {
    this.#check(providerId);
    // Disconnect is an admin action on the backend; the sidecar never deletes.
    await this.rpc.call('credential.delete', { provider_id: providerId });
  }
}
