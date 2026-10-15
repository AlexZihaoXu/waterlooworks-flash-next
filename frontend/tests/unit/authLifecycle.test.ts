import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  AuthGeneration,
  AuthQueue,
  parseSession,
  runAuthOperation,
  scheduleSessionRenewal,
} from '../../src/lib/authLifecycle';

function deferred() {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

afterEach(() => vi.useRealTimers());

describe('authLifecycle', () => {
  it('does not let a cancelled waiter release its still-running predecessor', async () => {
    vi.useFakeTimers();
    const queue = new AuthQueue();
    const first = deferred();
    const cancelled = new AbortController();
    const effects: string[] = [];
    const a = queue.run(async (operation) => {
      effects.push('a');
      await operation.wait(first.promise);
      effects.push('a-finished');
    });
    const b = queue.run(async () => {
      effects.push('b');
    }, cancelled.signal);
    const rejected = expect(b).rejects.toThrow();
    await vi.advanceTimersByTimeAsync(0);
    cancelled.abort();
    await rejected;
    const c = queue.run(async () => {
      effects.push('c');
    });
    await vi.advanceTimersByTimeAsync(0);
    expect(effects).toEqual(['a']);
    first.resolve();
    await Promise.all([a, c]);
    expect(effects).toEqual(['a', 'a-finished', 'c']);
  });
  it('invalidates stale exchange and renewal completion immediately on logout', () => {
    const lifecycle = new AuthGeneration();
    const exchange = lifecycle.begin();
    const renew = lifecycle.begin();
    expect(lifecycle.current(exchange)).toBe(false);
    lifecycle.begin(); // logout invalidates before any network operation finishes
    expect(lifecycle.current(renew)).toBe(false);
  });
  it('rejects missing or expired identity instead of opening the gate', () => {
    const live = Math.floor(Date.now() / 1000) + 60;
    for (const value of [
      null,
      {},
      { expires_at: 0 },
      // Admission state is part of the session contract, never assumed.
      { uid: 'a', role: 'member', expires_at: live },
      { uid: 'a', role: 'member', approval: 'declined', expires_at: live },
    ])
      expect(() => parseSession(value)).toThrow('Session unavailable.');
    expect(
      parseSession({
        uid: 'a',
        role: 'member',
        approval: 'pending',
        expires_at: live,
      }).approval,
    ).toBe('pending');
  });
  it.each(['import', 'token', 'exchange'])(
    'releases a stalled %s at the whole-operation deadline and fences late completion',
    async (stage) => {
      vi.useFakeTimers();
      const queue = new AuthQueue();
      const pending = deferred();
      const effects: string[] = [];
      const work = queue.run(async (operation) => {
        for (const step of ['import', 'token', 'exchange']) {
          await operation.wait(
            step === stage ? pending.promise : Promise.resolve(),
          );
          effects.push(step);
        }
        effects.push('publish');
      });
      const failed = expect(work).rejects.toThrow();
      await vi.advanceTimersByTimeAsync(15_000);
      await failed;
      const beforeLateCompletion = [...effects];
      await queue.run(async () => {
        effects.push('new-login');
      });
      pending.resolve();
      await vi.advanceTimersByTimeAsync(0);
      expect(effects).toEqual([...beforeLateCompletion, 'new-login']);
    },
  );
  it('exits checking after stalled initial module loading and never opens a late popup', async () => {
    vi.useFakeTimers();
    const module = deferred();
    const popup = vi.fn();
    let state = 'checking';
    const attempt = runAuthOperation(async (operation) => {
      await operation.wait(module.promise);
    }).then(
      () => {
        popup();
      },
      () => {
        state = 'error';
      },
    );
    await vi.advanceTimersByTimeAsync(15_000);
    await attempt;
    expect(state).toBe('error');
    module.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(popup).not.toHaveBeenCalled();
  });
  it.each(['logout', 'new login'])(
    'cancels initial module loading on %s before any auth work',
    async () => {
      const generation = new AuthGeneration();
      generation.begin();
      const module = deferred();
      const authWork = vi.fn();
      const attempt = runAuthOperation(async (operation) => {
        await operation.wait(module.promise);
      }, generation.signal).then(authWork);
      const failed = expect(attempt).rejects.toThrow();
      await Promise.resolve();
      generation.begin();
      await failed;
      module.resolve();
      await Promise.resolve();
      expect(authWork).not.toHaveBeenCalled();
    },
  );
  it('shares one deadline across successive asynchronous stages', async () => {
    vi.useFakeTimers();
    const queue = new AuthQueue();
    const first = deferred();
    const second = deferred();
    const publish = vi.fn();
    const work = queue.run(async (operation) => {
      await operation.wait(first.promise);
      await operation.wait(second.promise);
      publish();
    });
    const failed = expect(work).rejects.toThrow();
    await vi.advanceTimersByTimeAsync(10_000);
    first.resolve();
    await vi.advanceTimersByTimeAsync(5_000);
    await failed;
    second.resolve();
    await vi.advanceTimersByTimeAsync(0);
    expect(publish).not.toHaveBeenCalled();
  });
  it('includes queue wait in the deadline and never starts expired queued work', async () => {
    vi.useFakeTimers();
    const queue = new AuthQueue();
    const stalled = deferred();
    const first = queue.run(async (operation) =>
      operation.wait(stalled.promise),
    );
    const firstFailed = expect(first).rejects.toThrow();
    const started = vi.fn();
    const second = queue.run(async () => {
      started();
    });
    const secondFailed = expect(second).rejects.toThrow();
    await vi.advanceTimersByTimeAsync(15_000);
    await Promise.all([firstFailed, secondFailed]);
    expect(started).not.toHaveBeenCalled();
  });
  it.each(['resolve', 'reject'])(
    'logout releases pending work immediately and fences late %s',
    async (outcome) => {
      const generation = new AuthGeneration();
      generation.begin();
      const queue = new AuthQueue();
      const pending = deferred();
      const started = deferred();
      const publish = vi.fn();
      const old = queue.run(async (operation) => {
        started.resolve();
        await operation.wait(pending.promise);
        publish();
      }, generation.signal);
      const failed = expect(old).rejects.toThrow();
      await started.promise;
      generation.begin();
      await failed;
      const next = vi.fn();
      await queue.run(async () => {
        next();
      }, generation.signal);
      expect(next).toHaveBeenCalledOnce();
      if (outcome === 'resolve') pending.resolve();
      else pending.reject(new Error('primitive failure'));
      await Promise.resolve();
      expect(publish).not.toHaveBeenCalled();
    },
  );
  it('keeps expiry armed during a pending renewal and clears both timers on cleanup', async () => {
    vi.useFakeTimers();
    const pending = deferred();
    const renew = vi.fn(() => {
      void pending.promise;
    });
    const expire = vi.fn();
    const cleanup = scheduleSessionRenewal(
      Date.now() / 1000 + 61,
      renew,
      expire,
    );
    await vi.advanceTimersByTimeAsync(1_000);
    expect(renew).toHaveBeenCalledOnce();
    expect(expire).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(expire).toHaveBeenCalledOnce();
    cleanup();
    const cancel = scheduleSessionRenewal(
      Date.now() / 1000 + 61,
      renew,
      expire,
    );
    cancel();
    await vi.advanceTimersByTimeAsync(61_000);
    expect(renew).toHaveBeenCalledOnce();
    expect(expire).toHaveBeenCalledOnce();
    pending.resolve();
  });
});
