// A generation fences every asynchronous auth result, including logout/renew races.
export class AuthGeneration {
  private generation = 0;
  private controller = new AbortController();
  get signal() {
    return this.controller.signal;
  }
  begin() {
    this.controller.abort();
    this.controller = new AbortController();
    return ++this.generation;
  }
  current(generation: number) {
    return this.generation === generation;
  }
}

type AuthOperation = {
  signal: AbortSignal;
  check: () => void;
  wait: <T>(pending: Promise<T>) => Promise<T>;
};

// Bound imports/SDK calls even when their promises ignore cancellation.
export function runAuthOperation<T>(
  work: (operation: AuthOperation) => Promise<T>,
  signal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  const deadline = Date.now() + 15_000;
  const cancel = () => controller.abort();
  const timer = setTimeout(cancel, 15_000);
  const cancelled = new Promise<never>((_, reject) => {
    controller.signal.addEventListener(
      'abort',
      () => {
        reject(new Error('Authentication cancelled or timed out.'));
      },
      { once: true },
    );
  });
  signal?.addEventListener('abort', cancel, { once: true });
  if (signal?.aborted) cancel();
  const check = () => {
    if (Date.now() >= deadline) cancel();
    controller.signal.throwIfAborted();
  };
  const operation: AuthOperation = {
    signal: controller.signal,
    check,
    wait: async (pending) => {
      const value = await Promise.race([pending, cancelled]);
      check();
      return value;
    },
  };
  return operation
    .wait(
      Promise.resolve().then(() => {
        check();
        return work(operation);
      }),
    )
    .finally(() => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', cancel);
    });
}

// Each caller's deadline includes queue wait. The serialization barrier retains
// predecessors even when a waiting entry cancels; each active operation is bounded.
export class AuthQueue {
  private tail: Promise<void> = Promise.resolve();

  run(work: (operation: AuthOperation) => Promise<void>, signal?: AbortSignal) {
    const previous = this.tail;
    const next = runAuthOperation(async (operation) => {
      await operation.wait(previous);
      operation.check();
      await work(operation);
    }, signal);
    this.tail = Promise.all([previous, next.catch(() => {})]).then(() => {});
    return next;
  }
}

// Keep these timers owned by the session, including its refreshing transition.
export function scheduleSessionRenewal(
  expiresAt: number,
  renew: () => void,
  expire: () => void,
) {
  const remaining = Math.max(0, expiresAt * 1000 - Date.now());
  const expiry = setTimeout(expire, remaining);
  const renewal = setTimeout(renew, Math.max(0, remaining - 60_000));
  return () => {
    clearTimeout(expiry);
    clearTimeout(renewal);
  };
}

export type AppSession = {
  uid: string;
  role: 'admin' | 'member';
  expires_at: number;
};
export type AuthState =
  | { status: 'checking' | 'signed-out' | 'error' }
  | { status: 'ready' | 'refreshing'; session: AppSession };

export function parseSession(value: unknown): AppSession {
  if (
    typeof value !== 'object' ||
    value === null ||
    !('uid' in value) ||
    typeof value.uid !== 'string' ||
    !value.uid ||
    !('role' in value) ||
    !['admin', 'member'].includes(String(value.role)) ||
    !('expires_at' in value) ||
    typeof value.expires_at !== 'number' ||
    !Number.isSafeInteger(value.expires_at) ||
    value.expires_at * 1000 <= Date.now()
  )
    throw new Error('Session unavailable.');
  return value as AppSession;
}
