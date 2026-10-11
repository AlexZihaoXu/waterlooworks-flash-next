import { beforeEach, describe, expect, it, vi } from 'vitest';

const sdk = vi.hoisted(() => ({
  auth: {
    currentUser: null as null | {
      uid: string;
      getIdToken: (force?: boolean) => Promise<string>;
    },
    authStateReady: vi.fn(),
  },
  initializeAuth: vi.fn(),
}));

vi.mock('firebase/app', () => ({
  initializeApp: () => ({ options: {} }),
  deleteApp: async () => {},
}));
vi.mock('firebase/auth', () => ({
  browserLocalPersistence: 'browser-local',
  browserPopupRedirectResolver: 'popup-resolver',
  GoogleAuthProvider: class {
    setCustomParameters() {}
  },
  indexedDBLocalPersistence: 'indexed-db',
  inMemoryPersistence: 'in-memory',
  initializeAuth: (...args: unknown[]) => {
    sdk.initializeAuth(...args);
    return sdk.auth;
  },
  signInWithPopup: vi.fn(),
  signOut: vi.fn(),
}));

describe('firebase sign-in persistence', () => {
  beforeEach(() => {
    sdk.auth.currentUser = null;
    sdk.auth.authStateReady.mockReset();
  });

  it('keeps the Google sign-in across reloads for hourly renewal', async () => {
    await import('../../src/lib/firebase');
    expect(sdk.initializeAuth).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        persistence: ['indexed-db', 'browser-local', 'in-memory'],
      }),
    );
  });

  it('waits for the restored user before renewing after a reload', async () => {
    const { renewedToken } = await import('../../src/lib/firebase');
    const getIdToken = vi.fn(async () => 'opaque-renewal-placeholder');
    sdk.auth.authStateReady.mockImplementation(async () => {
      sdk.auth.currentUser = { uid: 'owner-uid', getIdToken };
    });
    await expect(renewedToken('owner-uid')).resolves.toBe(
      'opaque-renewal-placeholder',
    );
    expect(getIdToken).toHaveBeenCalledWith(true);
  });

  it('refuses to renew for a different restored account', async () => {
    const { renewedToken } = await import('../../src/lib/firebase');
    sdk.auth.authStateReady.mockImplementation(async () => {
      sdk.auth.currentUser = { uid: 'other-uid', getIdToken: vi.fn() };
    });
    await expect(renewedToken('owner-uid')).rejects.toThrow('Sign in again.');
  });
});
