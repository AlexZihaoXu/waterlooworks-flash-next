import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  AuthCancelled,
  AuthHttpError,
  isTransientAuthFailure,
  renewalRetryDelayMs,
} from '../../src/lib/authLifecycle';
import { useAppAuth } from '../../src/lib/useAppAuth';

// Identity-only session shapes and HTTP statuses; no tokens are fabricated
// beyond an opaque placeholder the mocked SDK hands to the mocked exchange.
vi.mock('../../src/lib/firebase', () => ({
  renewedToken: async () => 'opaque-renewal-placeholder',
  googleToken: async () => 'opaque-login-placeholder',
  signOutGoogle: async () => {},
}));

afterEach(() => vi.restoreAllMocks());

function json(body: object, status = 200, headers: HeadersInit = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

function session(secondsLeft: number, approval = 'approved') {
  return {
    uid: 'session-a',
    role: 'member',
    approval,
    expires_at: Math.floor(Date.now() / 1000) + secondsLeft,
  };
}

describe('transient auth failure classification', () => {
  it.each([429, 408, 500, 502, 503, 504])(
    'keeps the session through HTTP %s',
    (status) => {
      expect(isTransientAuthFailure(new AuthHttpError('session', status))).toBe(
        true,
      );
    },
  );
  it('keeps the session through network failures and timeouts', () => {
    expect(isTransientAuthFailure(new TypeError('Failed to fetch'))).toBe(true);
    expect(
      isTransientAuthFailure(new DOMException('timed out', 'TimeoutError')),
    ).toBe(true);
    expect(isTransientAuthFailure(new AuthCancelled())).toBe(true);
    expect(
      isTransientAuthFailure(
        Object.assign(new Error('x'), { code: 'auth/network-request-failed' }),
      ),
    ).toBe(true);
  });
  it('ends the session on refusals and invalid sessions', () => {
    expect(isTransientAuthFailure(new AuthHttpError('session', 401))).toBe(
      false,
    );
    expect(isTransientAuthFailure(new AuthHttpError('exchange', 403))).toBe(
      false,
    );
    expect(isTransientAuthFailure(new Error('Session unavailable.'))).toBe(
      false,
    );
    expect(
      isTransientAuthFailure(
        Object.assign(new Error('x'), { code: 'auth/user-token-expired' }),
      ),
    ).toBe(false);
  });
  it('honours a bounded Retry-After for renewal retries', () => {
    expect(renewalRetryDelayMs(new AuthHttpError('exchange', 503, 2))).toBe(
      2_000,
    );
    expect(renewalRetryDelayMs(new AuthHttpError('exchange', 429, 0))).toBe(
      1_000,
    );
    expect(renewalRetryDelayMs(new AuthHttpError('exchange', 429, 9999))).toBe(
      30_000,
    );
    expect(renewalRetryDelayMs(new TypeError('Failed to fetch'))).toBe(10_000);
  });
});

describe('useAppAuth session checks', () => {
  it('keeps a ready session through a rate-limited check and ends it on 401', async () => {
    const responses = [
      json(session(3600)),
      json({ detail: 'x' }, 429, { 'Retry-After': '2' }),
      json({ detail: 'x' }, 503),
      json({ detail: 'x' }, 401),
    ];
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (url) =>
        String(url).endsWith('/logout')
          ? json({})
          : (responses.shift() ?? json({ detail: 'x' }, 401)),
      );
    const { result } = renderHook(() => useAppAuth());
    await waitFor(() => expect(result.current.state.status).toBe('ready'));
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const calls = fetch.mock.calls.length;
      act(() => {
        window.dispatchEvent(new Event('focus'));
      });
      await waitFor(() => expect(fetch.mock.calls.length).toBe(calls + 1));
      await act(async () => {});
      expect(result.current.state.status).toBe('ready');
    }
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await waitFor(() =>
      expect(result.current.state).toEqual({
        status: 'signed-out',
        notice: 'session-ended',
      }),
    );
  });

  it('continues a pending session with its new access once approved', async () => {
    const responses = [json(session(3600, 'pending')), json(session(3600))];
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (url) =>
        String(url).endsWith('/logout')
          ? json({})
          : (responses.shift() ?? json(session(3600))),
      );
    const { result } = renderHook(() => useAppAuth());
    await waitFor(() => expect(result.current.state.status).toBe('ready'));
    expect(
      result.current.state.status === 'ready' &&
        result.current.state.session.approval,
    ).toBe('pending');
    act(() => {
      window.dispatchEvent(new Event('focus'));
    });
    await waitFor(() =>
      expect(
        result.current.state.status === 'ready' &&
          result.current.state.session.approval,
      ).toBe('approved'),
    );
    expect(
      fetch.mock.calls.some(([url]) => String(url).endsWith('/logout')),
    ).toBe(false);
  });

  it('retries a renewal refused with 503 instead of ending the session', async () => {
    const renewed = session(3600);
    const exchanges = [
      json({ detail: 'x' }, 503, { 'Retry-After': '1' }),
      json(renewed),
    ];
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async (url) => {
        const path = String(url);
        if (path.endsWith('/session')) return json(session(61));
        if (path.endsWith('/exchange'))
          return exchanges.shift() ?? json({ detail: 'x' }, 401);
        return json({});
      });
    const { result } = renderHook(() => useAppAuth());
    await waitFor(
      () =>
        expect(result.current.state).toEqual({
          status: 'ready',
          session: renewed,
        }),
      { timeout: 6_000 },
    );
    const exchangeCalls = fetch.mock.calls.filter(([url]) =>
      String(url).endsWith('/exchange'),
    );
    expect(exchangeCalls).toHaveLength(2);
    expect(
      fetch.mock.calls.some(([url]) => String(url).endsWith('/logout')),
    ).toBe(false);
  }, 10_000);
});
