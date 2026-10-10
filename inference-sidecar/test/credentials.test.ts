import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  createModels,
  createProvider,
  fauxProvider,
  type Credential,
} from '@earendil-works/pi-ai';
import {
  RpcCredentialStore,
  parseCredential,
  type RpcCaller,
} from '../src/credentials.js';
import { RpcError } from '../src/channel.js';

function fauxStreams(faux: ReturnType<typeof fauxProvider>) {
  return {
    stream: faux.provider.stream.bind(faux.provider),
    streamSimple: faux.provider.streamSimple.bind(faux.provider),
  } as Parameters<typeof createProvider>[0]['api'] & object;
}

/** In-memory stand-in for the backend's encrypted credential RPC. */
class FakeBackendStore implements RpcCaller {
  persisted = new Map<string, Credential>();
  locks = new Map<string, string>();
  calls: string[] = [];
  /** Commits that fail like a briefly locked database before succeeding. */
  failCommits = 0;

  async call(method: string, params: Record<string, unknown>) {
    this.calls.push(method);
    const provider = params.provider_id as string;
    switch (method) {
      case 'credential.read':
        return { credential: this.persisted.get(provider) ?? null };
      case 'credential.list':
        return {
          credentials: [...this.persisted].map(([id, c]) => ({
            provider_id: id,
            type: c.type,
          })),
        };
      case 'credential.lock': {
        assert.equal(
          [...this.locks.values()].includes(provider),
          false,
          'double lock',
        );
        const lock = 'a'.repeat(31) + String(this.locks.size);
        this.locks.set(lock, provider);
        return { lock, credential: this.persisted.get(provider) ?? null };
      }
      case 'credential.commit': {
        const owner = this.locks.get(params.lock as string);
        assert.ok(owner);
        if (this.failCommits > 0) {
          this.failCommits -= 1;
          throw new RpcError('credential_unavailable');
        }
        // Persist BEFORE acknowledging, like the backend's locked transaction.
        this.persisted.set(owner, params.credential as Credential);
        this.locks.delete(params.lock as string);
        return {};
      }
      case 'credential.release':
        this.locks.delete(params.lock as string);
        return {};
      case 'credential.delete':
        throw new RpcError('refused');
      default:
        throw new RpcError('refused');
    }
  }
}

test('expired OAuth refresh rotates the token through modify and persists it', async () => {
  const backend = new FakeBackendStore();
  backend.persisted.set('rotating', {
    type: 'oauth',
    access: 'old-access',
    refresh: 'old-refresh',
    expires: Date.now() - 1000,
  });
  let refreshes = 0;
  const faux = fauxProvider({ provider: 'rotating' });
  const provider = createProvider({
    id: 'rotating',
    auth: {
      oauth: {
        name: 'Rotating test OAuth',
        login: async () => {
          throw new Error('not used');
        },
        refresh: async (credential) => {
          refreshes += 1;
          assert.equal(credential.refresh, 'old-refresh');
          return {
            type: 'oauth',
            access: 'new-access',
            refresh: 'new-refresh',
            expires: Date.now() + 3_600_000,
          };
        },
        toAuth: async (credential) => ({ apiKey: credential.access }),
      },
    },
    models: faux.models,
    api: fauxStreams(faux),
  });
  const models = createModels({ credentials: new RpcCredentialStore(backend) });
  models.setProvider(provider);
  const [first, second] = await Promise.all([
    models.getAuth('rotating'),
    models.getAuth('rotating'),
  ]);
  assert.equal(first?.auth.apiKey, 'new-access');
  assert.equal(second?.auth.apiKey, 'new-access');
  assert.equal(refreshes, 1, 'serialized modify must not double-refresh');
  const stored = backend.persisted.get('rotating') as Credential & {
    refresh: string;
  };
  assert.equal(stored.refresh, 'new-refresh');
  assert.equal(backend.locks.size, 0);
  assert.ok(backend.calls.includes('credential.commit'));
});

test('stored api-key credential resolves request auth without environment', async () => {
  const backend = new FakeBackendStore();
  backend.persisted.set('custom', { type: 'api_key', key: 'k-123' });
  const faux = fauxProvider({ provider: 'custom' });
  const models = createModels({ credentials: new RpcCredentialStore(backend) });
  models.setProvider(
    createProvider({
      id: 'custom',
      auth: {
        apiKey: {
          name: 'token',
          resolve: async ({ credential }) => ({
            auth: { apiKey: credential?.key ?? 'unused' },
          }),
        },
      },
      models: faux.models,
      api: fauxStreams(faux),
    }),
  );
  const auth = await models.getAuth('custom');
  assert.equal(auth?.auth.apiKey, 'k-123');
});

test('failed modify releases the backend lock and never commits', async () => {
  const backend = new FakeBackendStore();
  const store = new RpcCredentialStore(backend);
  await assert.rejects(
    store.modify('openai', async () => {
      throw new Error('refresh failed');
    }),
  );
  assert.equal(backend.locks.size, 0);
  assert.equal(backend.calls.includes('credential.commit'), false);
  assert.ok(backend.calls.includes('credential.release'));
});

test('credential shapes are strict and the sidecar cannot delete', async () => {
  assert.throws(() => parseCredential({ type: 'api_key', key: 'x', extra: 1 }));
  assert.throws(() => parseCredential({ type: 'oauth', access: 'a' }));
  assert.throws(() => parseCredential('string'));
  assert.equal(parseCredential(null), undefined);
  const store = new RpcCredentialStore(new FakeBackendStore());
  await assert.rejects(store.delete('openai'));
  await assert.rejects(store.read('../etc'));
});

test('a transiently failing commit is retried under the same lock', async () => {
  const backend = new FakeBackendStore();
  backend.failCommits = 2;
  const store = new RpcCredentialStore(backend, [0, 0, 0]);
  const rotated: Credential = {
    type: 'oauth',
    access: 'new-access',
    refresh: 'rotated-refresh',
    expires: Date.now() + 3_600_000,
  };
  await store.modify('rotating', async () => rotated);
  assert.deepEqual(backend.persisted.get('rotating'), rotated);
  assert.equal(backend.calls.filter((c) => c === 'credential.lock').length, 1);
  assert.equal(
    backend.calls.filter((c) => c === 'credential.commit').length,
    3,
  );
  assert.equal(backend.calls.includes('credential.release'), false);
  assert.equal(backend.locks.size, 0);
});

test('exhausted commit retries release the lock and reject', async () => {
  const backend = new FakeBackendStore();
  backend.failCommits = 10;
  const store = new RpcCredentialStore(backend, [0, 0]);
  await assert.rejects(
    store.modify('rotating', async () => ({
      type: 'oauth',
      access: 'a',
      refresh: 'r',
      expires: Date.now() + 1000,
    })),
  );
  assert.equal(
    backend.calls.filter((c) => c === 'credential.commit').length,
    3,
  );
  assert.ok(backend.calls.includes('credential.release'));
  assert.equal(backend.locks.size, 0);
});
