import { afterEach, describe, expect, it, vi } from 'vitest';
import { readManualRefresh } from '../../src/lib/workspaceRefresh';

function deferredResponse() {
  let resolve!: (response: Response) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Response>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

afterEach(() => vi.restoreAllMocks());

describe('workspaceRefresh lifecycle (HTTP/empty-data primitives only)', () => {
  it.each(['resolve', 'reject'])(
    'aborts pending POST and prevents follow-up GET after unmount and late %s',
    async (outcome) => {
      const pending = deferredResponse();
      const fetch = vi
        .spyOn(globalThis, 'fetch')
        .mockImplementation(() => pending.promise);
      const mount = new AbortController();
      const publish = vi.fn();
      const old = readManualRefresh(mount.signal).then(publish);
      const failed = expect(old).rejects.toThrow();
      expect(fetch.mock.calls[0]?.[1]?.signal).toBe(mount.signal);
      mount.abort();
      if (outcome === 'resolve')
        pending.resolve(new Response(null, { status: 202 }));
      else pending.reject(new Error('primitive failure'));
      await failed;
      expect(fetch).toHaveBeenCalledTimes(1);
      expect(publish).not.toHaveBeenCalled();
    },
  );
  it('uses the same lifecycle for GET and fences its late empty response', async () => {
    const pending = deferredResponse();
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(null, { status: 202 }))
      .mockImplementationOnce(() => pending.promise);
    const mount = new AbortController();
    const publish = vi.fn();
    const old = readManualRefresh(mount.signal).then(publish);
    const failed = expect(old).rejects.toThrow();
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls[1]?.[1]?.signal).toBe(mount.signal);
    mount.abort();
    pending.resolve(
      new Response('null', { headers: { 'Content-Type': 'application/json' } }),
    );
    await failed;
    expect(publish).not.toHaveBeenCalled();
  });
  it('fences a status body that finishes after cancellation', async () => {
    let body!: ReadableStreamDefaultController<Uint8Array>;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        body = controller;
      },
    });
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(null, { status: 202 }))
      .mockResolvedValueOnce(
        new Response(stream, {
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    const mount = new AbortController();
    const publish = vi.fn();
    const old = readManualRefresh(mount.signal).then(publish);
    const failed = expect(old).rejects.toThrow();
    await Promise.resolve();
    await Promise.resolve();
    mount.abort();
    body.enqueue(new TextEncoder().encode('null'));
    body.close();
    await failed;
    expect(publish).not.toHaveBeenCalled();
  });
  it('does not let an old mount resume after a new mount starts', async () => {
    const pending = deferredResponse();
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementationOnce(() => pending.promise)
      .mockResolvedValueOnce(new Response(null, { status: 202 }))
      .mockResolvedValueOnce(
        new Response('null', {
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    const oldMount = new AbortController();
    const old = readManualRefresh(oldMount.signal);
    const failed = expect(old).rejects.toThrow();
    oldMount.abort();
    const newMount = new AbortController();
    await expect(readManualRefresh(newMount.signal)).resolves.toEqual({
      status: 202,
      snapshot: null,
      recentFinishedAt: null,
    });
    pending.resolve(new Response(null, { status: 202 }));
    await failed;
    expect(newMount.signal.aborted).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(3);
    await expect(readManualRefresh(oldMount.signal)).rejects.toThrow();
    expect(fetch).toHaveBeenCalledTimes(3);
  });
});
