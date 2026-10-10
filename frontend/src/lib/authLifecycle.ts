/** A bounded auth operation timed out or was superseded; never server text. */
export class AuthCancelled extends Error {
  constructor() {
    super('Authentication cancelled or timed out.');
  }
}

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
        reject(new AuthCancelled());
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
// 'rejected': the exchange refused the Google identity or the prepared
// reservation (401). 'owner-required': the empty instance accepts only its
// configured owner as the first administrator (403).
export type SignInFailure =
  'cancelled' | 'rejected' | 'owner-required' | 'unavailable';

/** HTTP refusal from an auth route; never carries server-supplied text. */
export class AuthHttpError extends Error {
  readonly path: string;
  readonly status: number;
  readonly retryAfter: number | null;
  constructor(path: string, status: number, retryAfter: number | null = null) {
    super('Authentication unavailable.');
    this.path = path;
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

/** Seconds from a delta-seconds Retry-After header; dates/garbage are ignored. */
export function parseRetryAfter(value: string | null): number | null {
  if (value === null || !/^\d{1,6}$/.test(value.trim())) return null;
  return Number(value.trim());
}

function errorCode(error: unknown) {
  return typeof error === 'object' &&
    error !== null &&
    'code' in error &&
    typeof error.code === 'string'
    ? error.code
    : null;
}

// A rate limit, overload, outage, timeout or dropped connection says nothing
// about the session itself, so checks and renewals keep it and retry. Only an
// explicit refusal (401/403) or an invalid session/token ends it.
export function isTransientAuthFailure(error: unknown): boolean {
  if (error instanceof AuthHttpError)
    return error.status === 408 || error.status === 429 || error.status >= 500;
  if (error instanceof AuthCancelled || error instanceof TypeError) return true;
  if (
    error instanceof DOMException &&
    (error.name === 'TimeoutError' || error.name === 'AbortError')
  )
    return true;
  return errorCode(error) === 'auth/network-request-failed';
}

export function renewalRetryDelayMs(error: unknown): number {
  const seconds =
    error instanceof AuthHttpError && error.retryAfter !== null
      ? Math.min(Math.max(error.retryAfter, 1), 30)
      : 10;
  return seconds * 1000;
}

const cancelledCodes = new Set([
  'auth/popup-closed-by-user',
  'auth/cancelled-popup-request',
  'auth/user-cancelled',
]);

// Only the exchange's own refusals are specific; configuration, rate limits
// and transport stay "unavailable".
export function classifySignInError(error: unknown): SignInFailure {
  if (error instanceof AuthHttpError) {
    if (error.path !== 'exchange') return 'unavailable';
    if (error.status === 401) return 'rejected';
    if (error.status === 403) return 'owner-required';
    return 'unavailable';
  }
  const code = errorCode(error);
  return code !== null && cancelledCodes.has(code)
    ? 'cancelled'
    : 'unavailable';
}

export type AuthState =
  | { status: 'checking' }
  | { status: 'error'; reason?: SignInFailure }
  // 'session-ended': renewal, expiry or a session check ended the session
  // without an explicit sign-out; the sign-in card explains it.
  | { status: 'signed-out'; notice?: 'session-ended' }
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
