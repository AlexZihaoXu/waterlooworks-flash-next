import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AuthGeneration,
  parseRetryAfter,
  runAuthOperation,
} from './authLifecycle';
import type { components } from './api.generated';

export type ApiTokenStatus = components['schemas']['ApiTokenStatus'];
export type ApiTokenIssued = components['schemas']['ApiTokenIssued'];

const endpoint = '/api/account/api-token';
const TOKEN = /^wwf_[A-Za-z0-9_-]{43}$/;
/** A shown token is tab memory only and clears itself after five minutes. */
export const TOKEN_REVEAL_MS = 5 * 60_000;

const isTime = (value: unknown) =>
  typeof value === 'string' &&
  value.length <= 64 &&
  !Number.isNaN(Date.parse(value));
const optionalTime = (value: unknown) =>
  value === undefined || value === null || isTime(value);
const isHint = (value: unknown) =>
  typeof value === 'string' && value.length <= 32 && value.startsWith('wwf_');

export function parseTokenStatus(value: unknown): ApiTokenStatus {
  if (!value || typeof value !== 'object')
    throw new Error('Token status unavailable.');
  const data = value as Record<string, unknown>;
  const allowed = ['configured', 'hint', 'created_at', 'last_used_at'];
  if (
    Object.keys(data).some((key) => !allowed.includes(key)) ||
    typeof data.configured !== 'boolean' ||
    !(data.hint === undefined || data.hint === null || isHint(data.hint)) ||
    !optionalTime(data.created_at) ||
    !optionalTime(data.last_used_at)
  )
    throw new Error('Token status unavailable.');
  return {
    configured: data.configured,
    hint: (data.hint as string | null | undefined) ?? null,
    created_at: (data.created_at as string | null | undefined) ?? null,
    last_used_at: (data.last_used_at as string | null | undefined) ?? null,
  };
}

export function parseIssuedToken(value: unknown): ApiTokenIssued {
  if (!value || typeof value !== 'object')
    throw new Error('Token unavailable.');
  const data = value as Record<string, unknown>;
  if (
    Object.keys(data).length !== 3 ||
    typeof data.token !== 'string' ||
    !TOKEN.test(data.token) ||
    !isHint(data.hint) ||
    !isTime(data.created_at)
  )
    throw new Error('Token unavailable.');
  return data as ApiTokenIssued;
}

class TokenHttpError extends Error {
  readonly status: number;
  readonly retryAfter: number | null;
  constructor(status: number, retryAfter: number | null) {
    super('Token request refused.');
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

function request(
  path: string,
  signal: AbortSignal,
  method: 'GET' | 'POST' | 'DELETE' = 'GET',
): Promise<unknown> {
  return runAuthOperation(async (operation) => {
    const response = await operation.wait(
      fetch(path, {
        method,
        body: method === 'GET' ? undefined : '{}',
        headers:
          method === 'GET'
            ? undefined
            : { 'Content-Type': 'application/json', 'X-WW-Request': '1' },
        credentials: 'same-origin',
        cache: 'no-store',
        signal: operation.signal,
      }),
    );
    // Server text is never shown: only the status and Retry-After are used.
    if (!response.ok)
      throw new TokenHttpError(
        response.status,
        parseRetryAfter(response.headers.get('Retry-After')),
      );
    return operation.wait(response.json());
  }, signal);
}

export function rateLimitMessage(retryAfter: number | null): string {
  if (retryAfter === null) return 'Too many token resets. Try again later.';
  const minutes = Math.max(1, Math.ceil(retryAfter / 60));
  return `Too many token resets. Try again in about ${minutes} minute${minutes === 1 ? '' : 's'}.`;
}

/**
 * The signed-in user's personal agent API token: metadata while settings are
 * open, and a freshly minted plaintext token held only in memory until it is
 * hidden, settings close, the tab is hidden, the account changes, or five
 * minutes pass. Never persisted or logged.
 */
export function useApiToken(isOpen: boolean, uid: string) {
  const lifecycle = useRef(new AuthGeneration());
  const busy = useRef(false);
  const [status, setStatus] = useState<ApiTokenStatus | null>(null);
  const [issued, setIssued] = useState<ApiTokenIssued | null>(null);
  const [copied, setCopied] = useState(false);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');

  const hide = useCallback(() => {
    setIssued(null);
    setCopied(false);
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    const lifetime = lifecycle.current;
    const generation = lifetime.begin();
    queueMicrotask(() => {
      if (!lifetime.current(generation)) return;
      busy.current = true;
      setPending(true);
      void request(endpoint, lifetime.signal)
        .then(parseTokenStatus)
        .then((loaded) => {
          if (lifetime.current(generation)) setStatus(loaded);
        })
        .catch(() => {
          if (lifetime.current(generation))
            setMessage('Token status unavailable. Reopen settings to retry.');
        })
        .finally(() => {
          if (lifetime.current(generation)) {
            busy.current = false;
            setPending(false);
          }
        });
    });
    return () => {
      lifetime.begin();
      busy.current = false;
      setStatus(null);
      setIssued(null);
      setCopied(false);
      setPending(false);
      setMessage('');
    };
  }, [isOpen, uid]);

  useEffect(() => {
    if (!issued) return;
    const timer = setTimeout(hide, TOKEN_REVEAL_MS);
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') hide();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [issued, hide]);

  const run = async (work: (signal: AbortSignal) => Promise<void>) => {
    if (!isOpen || busy.current) return;
    const lifetime = lifecycle.current;
    const generation = lifetime.begin();
    busy.current = true;
    setPending(true);
    setMessage('');
    try {
      await work(lifetime.signal);
    } catch (error) {
      if (!lifetime.current(generation)) return;
      setMessage(
        error instanceof TokenHttpError && error.status === 429
          ? rateLimitMessage(error.retryAfter)
          : 'The token change could not be confirmed. Reopen settings to check its status.',
      );
    } finally {
      if (lifetime.current(generation)) {
        busy.current = false;
        setPending(false);
      }
    }
  };

  const reset = () =>
    run(async (signal) => {
      hide();
      const minted = parseIssuedToken(
        await request(`${endpoint}/reset`, signal, 'POST'),
      );
      if (signal.aborted) return;
      const metadata = {
        configured: true,
        hint: minted.hint,
        created_at: minted.created_at,
        last_used_at: null,
      };
      setStatus(metadata);
      // Never surface a secret into a hidden tab; the old token is gone too.
      if (document.visibilityState === 'hidden') {
        setMessage(
          'A new token was created while this tab was hidden, so it was not shown. Reset it again to get one.',
        );
        return;
      }
      setIssued(minted);
    });

  const revoke = () =>
    run(async (signal) => {
      hide();
      const revoked = parseTokenStatus(
        await request(endpoint, signal, 'DELETE'),
      );
      if (signal.aborted) return;
      setStatus(revoked);
      setMessage('Token revoked. Tools using it can no longer sign in.');
    });

  return {
    status,
    issued,
    copied,
    setCopied,
    pending,
    message,
    setMessage,
    reset,
    revoke,
    hide,
  };
}

export type ApiTokenSettings = ReturnType<typeof useApiToken>;

/** Settings dismissal wording while a freshly shown token is uncopied. */
export const uncopiedTokenDiscard = {
  heading: 'Close without copying?',
  body: 'Your new API token has not been copied. It cannot be shown again after settings close; you would have to reset it.',
  confirm: 'Close anyway',
  cancel: 'Keep open',
};
