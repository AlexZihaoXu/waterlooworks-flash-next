import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  scheduleRevealHide,
  SECRET_MASK,
} from '../../src/lib/openaiRevealState';
import { useOpenAISettings } from '../../src/lib/useOpenAISettings';

const empty = { configured: false, status: 'not_configured', updated_at: null };
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('OpenAI admin configuration without provider authentication', () => {
  it('clears random key drafts on save, close and account change; never saves a mask', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(() => Promise.resolve(Response.json(empty)));
    const { result, rerender } = renderHook(
      ({ open, uid }) => useOpenAISettings(open, uid),
      { initialProps: { open: true, uid: 'admin-a' } },
    );
    await waitFor(() => expect(result.current.pending).toBe(false));
    act(() => result.current.setDraft(SECRET_MASK));
    await act(async () => result.current.save());
    expect(fetch).toHaveBeenCalledTimes(1);
    act(() => result.current.setDraft(crypto.randomUUID()));
    await act(async () => result.current.save());
    expect(result.current.draft).toBe('');
    expect(fetch.mock.calls[1][1]).toMatchObject({
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-WW-Request': '1' },
    });
    act(() => result.current.setDraft(crypto.randomUUID()));
    rerender({ open: false, uid: 'admin-a' });
    expect(result.current.draft).toBe('');
    rerender({ open: true, uid: 'admin-a' });
    await waitFor(() => expect(result.current.pending).toBe(false));
    act(() => result.current.setDraft(crypto.randomUUID()));
    rerender({ open: true, uid: 'admin-b' });
    expect(result.current.draft).toBe('');
  });

  it('bounds ignored transport and fences late status after close', async () => {
    vi.useFakeTimers();
    let resolve!: (value: Response) => void;
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const { result, rerender } = renderHook(
      ({ open }) => useOpenAISettings(open, 'admin-a'),
      { initialProps: { open: true } },
    );
    await act(async () => vi.advanceTimersByTimeAsync(15_000));
    expect(result.current.pending).toBe(false);
    expect(fetch.mock.calls[0][1]?.signal?.aborted).toBe(true);
    rerender({ open: false });
    await act(async () => resolve(Response.json(empty)));
    expect(result.current.message).toBe('');
    expect(result.current.readyToVerify).toBe(false);
  });

  it('hides ephemeral plaintext at 30 seconds and on visibility hidden; cleanup detaches', () => {
    vi.useFakeTimers();
    const hide = vi.fn();
    const dispose = scheduleRevealHide(hide);
    vi.advanceTimersByTime(29_999);
    expect(hide).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(hide).toHaveBeenCalledOnce();
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    document.dispatchEvent(new Event('visibilitychange'));
    expect(hide).toHaveBeenCalledTimes(2);
    dispose();
    document.dispatchEvent(new Event('visibilitychange'));
    expect(hide).toHaveBeenCalledTimes(2);
  });
});

// Opaque asynchronous primitives only: no fabricated provider identity/token result.
describe('interactive verification cancellation', () => {
  it('does not time out human interaction at 15 seconds and rejects late completion after close', async () => {
    const { waitForInteractiveVerification } =
      await import('../../src/lib/openaiRevealState');
    vi.useFakeTimers();
    const controller = new AbortController();
    let complete!: () => void;
    const pending = new Promise<void>((resolve) => {
      complete = resolve;
    });
    const settled = vi.fn();
    const work = waitForInteractiveVerification(
      pending,
      controller.signal,
    ).then(settled, settled);
    await vi.advanceTimersByTimeAsync(30_000);
    expect(settled).not.toHaveBeenCalled();
    controller.abort();
    await work;
    expect(settled).toHaveBeenCalledOnce();
    expect(settled.mock.calls[0][0]).toBeInstanceOf(Error);
    complete();
    await Promise.resolve();
    expect(settled).toHaveBeenCalledOnce();
  });

  it('suppresses failed or dismissed interactive work and rejects already-cancelled work', async () => {
    const { waitForInteractiveVerification } =
      await import('../../src/lib/openaiRevealState');
    const controller = new AbortController();
    const diagnostic = crypto.randomUUID();
    await expect(
      waitForInteractiveVerification(
        Promise.reject(new Error(diagnostic)),
        controller.signal,
      ),
    ).rejects.toThrow('Verification was not completed.');
    controller.abort();
    await expect(
      waitForInteractiveVerification(Promise.resolve(), controller.signal),
    ).rejects.toThrow('Verification cancelled.');
  });

  it('parses only bounded challenge and dedicated plaintext response primitives', async () => {
    const { parseRevealChallenge, parseRevealedKey } =
      await import('../../src/lib/useOpenAISettings');
    const secret = crypto.randomUUID();
    expect(parseRevealedKey({ api_key: secret })).toBe(secret);
    for (const invalid of [
      { api_key: secret, password: secret },
      { api_key: '' },
      { api_key: secret.repeat(20) },
      { password: secret },
    ]) {
      expect(() => parseRevealedKey(invalid)).toThrow('Reveal unavailable.');
    }
    const nonce = crypto.randomUUID().replaceAll('-', '') + 'a'.repeat(11);
    const challenge = { challenge: nonce, not_before: 101, expires_at: 400 };
    expect(parseRevealChallenge(challenge)).toEqual(challenge);
    for (const invalid of [
      { ...challenge, not_before: 100.5 },
      { ...challenge, expires_at: 402 },
      { ...challenge, password: secret },
      { ...challenge, challenge: '' },
    ]) {
      expect(() => parseRevealChallenge(invalid)).toThrow(
        'Verification unavailable.',
      );
    }
  });
});
