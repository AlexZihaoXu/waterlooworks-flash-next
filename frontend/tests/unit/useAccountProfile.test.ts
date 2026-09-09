import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useAccountProfile } from '../../src/lib/useAccountProfile';

afterEach(() => vi.restoreAllMocks());

describe('useAccountProfile optional read lifecycle', () => {
  it('does not fetch without a ready UID, and treats network failure as absent profile', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockRejectedValue(new TypeError('Network unavailable'));
    const { result, rerender } = renderHook(
      ({ uid }) => useAccountProfile(uid),
      { initialProps: { uid: null as string | null } },
    );
    expect(fetch).not.toHaveBeenCalled();
    rerender({ uid: 'session-a' });
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(result.current).toBeNull();
    expect(fetch).toHaveBeenCalledWith(
      '/api/auth/profile',
      expect.objectContaining({
        cache: 'no-store',
        credentials: 'same-origin',
      }),
    );
  });

  it('aborts logout/account changes and fences late empty responses even if transport ignores abort', async () => {
    const pending: Array<{
      signal: AbortSignal;
      resolve: (response: Response) => void;
    }> = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      (_url, options) =>
        new Promise((resolve) => {
          pending.push({ signal: options!.signal!, resolve });
        }),
    );
    const { result, rerender } = renderHook(
      ({ uid }) => useAccountProfile(uid),
      { initialProps: { uid: 'session-a' as string | null } },
    );
    await waitFor(() => expect(pending).toHaveLength(1));
    rerender({ uid: 'session-b' });
    expect(pending[0].signal.aborted).toBe(true);
    await waitFor(() => expect(pending).toHaveLength(2));
    await act(async () => pending[0].resolve(Response.json({})));
    expect(result.current).toBeNull();
    rerender({ uid: null });
    expect(pending[1].signal.aborted).toBe(true);
    await act(async () => pending[1].resolve(Response.json({})));
    expect(result.current).toBeNull();
  });

  it('reads an empty profile without persisting it and clears it on account change', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
      Promise.resolve(Response.json({})),
    );
    const save = vi.spyOn(Storage.prototype, 'setItem');
    const { result, rerender } = renderHook(
      ({ uid }) => useAccountProfile(uid),
      { initialProps: { uid: 'session-a' as string | null } },
    );
    await waitFor(() =>
      expect(result.current).toEqual({
        display_name: null,
        email: null,
        photo_url: null,
      }),
    );
    rerender({ uid: null });
    expect(result.current).toBeNull();
    expect(save).not.toHaveBeenCalled();
  });
});
