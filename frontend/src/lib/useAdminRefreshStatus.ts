import { useCallback, useEffect, useRef, useState } from 'react';
import { AuthGeneration, runAuthOperation } from './authLifecycle';
import type { components } from './api.generated';

type Challenge = Omit<components['schemas']['AdminRefreshStatus'], 'pacing'>;
type Usage = components['schemas']['OpenAIUsageStatus'];
export type RefreshPacing = components['schemas']['RefreshPacingStatus'];

const PACING_STOPS = ['request_cap', 'daily_cap', 'ban_risk'];

/** Strict pacing status: counters, fixed stop labels and ISO timestamps only. */
export function parseRefreshPacing(value: unknown): RefreshPacing {
  if (!value || typeof value !== 'object')
    throw new Error('Refresh status unavailable.');
  const data = value as Record<string, unknown>;
  const count = (key: string) =>
    typeof data[key] === 'number' &&
    Number.isSafeInteger(data[key]) &&
    (data[key] as number) >= 0;
  const seconds = (key: string) =>
    typeof data[key] === 'number' &&
    Number.isFinite(data[key]) &&
    (data[key] as number) >= 0;
  const time = (key: string) =>
    data[key] === null ||
    (typeof data[key] === 'string' &&
      (data[key] as string).length <= 64 &&
      !Number.isNaN(Date.parse(data[key] as string)));
  if (
    Object.keys(data).length !== 17 ||
    ![
      'request_cap',
      'daily_request_cap',
      'min_interval_minutes',
      'rest_every_min',
      'rest_every_max',
      'used_24h',
      'remaining_24h',
    ].every(count) ||
    ![
      'min_gap_seconds',
      'max_gap_seconds',
      'rest_min_seconds',
      'rest_max_seconds',
    ].every(seconds) ||
    ![
      'next_allowed_at',
      'last_finished_at',
      'cooldown_until',
      'blocked_until',
    ].every(time) ||
    !(
      data.last_pacing_stop === null ||
      PACING_STOPS.includes(String(data.last_pacing_stop))
    ) ||
    !(
      data.last_error_category === null ||
      (typeof data.last_error_category === 'string' &&
        /^[a-z_]{1,40}$/.test(data.last_error_category))
    )
  )
    throw new Error('Refresh status unavailable.');
  return data as RefreshPacing;
}

function optionalPacing(value: unknown): RefreshPacing | null {
  if (value === undefined) return null;
  try {
    return parseRefreshPacing(value);
  } catch {
    return null;
  }
}

/** The Duo challenge part; an attached pacing object is validated separately. */
export function parseRefreshAuth(value: unknown): Challenge {
  if (!value || typeof value !== 'object')
    throw new Error('Refresh status unavailable.');
  const data = value as Record<string, unknown>;
  const keys = Object.keys(data).filter((key) => key !== 'pacing');
  if (
    keys.length !== 4 ||
    Object.keys(data).length - keys.length > 1 ||
    !(
      data.run_id === null ||
      (typeof data.run_id === 'string' &&
        /^[a-zA-Z0-9-]{1,64}$/.test(data.run_id))
    ) ||
    typeof data.approval_required !== 'boolean' ||
    !(
      data.code === null ||
      (typeof data.code === 'string' && /^[0-9]{3}$/.test(data.code))
    ) ||
    typeof data.expires_in_seconds !== 'number' ||
    !Number.isFinite(data.expires_in_seconds) ||
    data.expires_in_seconds < 0 ||
    data.expires_in_seconds > 180 ||
    (data.code !== null &&
      (!data.approval_required ||
        data.run_id === null ||
        data.expires_in_seconds === 0))
  )
    throw new Error('Refresh status unavailable.');
  return {
    run_id: data.run_id as Challenge['run_id'],
    approval_required: data.approval_required as boolean,
    code: data.code as Challenge['code'],
    expires_in_seconds: data.expires_in_seconds as number,
  };
}

export function parseOpenAIUsage(value: unknown): Usage {
  if (!value || typeof value !== 'object')
    throw new Error('Usage unavailable.');
  const data = value as Usage;
  const count = (value: unknown) =>
    typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
  const usd = (value: unknown) =>
    typeof value === 'string' &&
    /^[0-9]{1,30}(\.[0-9]{1,30})?([eE][+-]?[0-9]{1,2})?$/.test(value);
  if (
    !count(data.default_request_limit) ||
    !usd(data.default_known_cost_stop_usd) ||
    // Daily-stop fields are optional on read; when the stop is set, its
    // rolling totals must be valid too.
    (data.daily_cost_stop_invalid !== undefined &&
      typeof data.daily_cost_stop_invalid !== 'boolean') ||
    (data.daily_cost_stop_usd !== undefined &&
      data.daily_cost_stop_usd !== null &&
      (!usd(data.daily_cost_stop_usd) ||
        !usd(data.rolling_24h_known_usd) ||
        !count(data.rolling_24h_attempts) ||
        !count(data.rolling_24h_unpriced_attempts))) ||
    !Array.isArray(data.runs) ||
    data.runs.length > 20 ||
    !data.runs.every(
      (run) =>
        run &&
        typeof run.run_id === 'string' &&
        /^[a-zA-Z0-9-]{1,64}$/.test(run.run_id) &&
        count(run.request_limit) &&
        usd(run.known_cost_stop_usd) &&
        count(run.attempts) &&
        count(run.unpriced_attempts) &&
        usd(run.known_usd) &&
        (run.total_usd === null || usd(run.total_usd)) &&
        [
          run.input_tokens,
          run.output_tokens,
          run.cached_tokens,
          run.cache_write_tokens,
          run.reasoning_tokens,
        ].every((value) => value === null || count(value)),
    )
  )
    throw new Error('Usage unavailable.');
  return data;
}

function read(path: string, signal: AbortSignal): Promise<unknown> {
  return runAuthOperation(async (operation) => {
    const response = await operation.wait(
      fetch(path, {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store',
        signal: operation.signal,
      }),
    );
    if (!response.ok) throw new Error('Admin status unavailable.');
    return operation.wait(response.json());
  }, signal);
}

// Mounted only by the admin account composition. No browser persistence or SSE.
export function useAdminRefreshStatus(isOpen: boolean, uid: string) {
  const lifecycle = useRef(new AuthGeneration());
  const expiry = useRef<ReturnType<typeof setTimeout> | null>(null);
  const poll = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [pacing, setPacing] = useState<RefreshPacing | null>(null);
  // Distinguishes a failed usage read from one still loading (null usage).
  const [usageError, setUsageError] = useState(false);
  const [message, setMessage] = useState('');
  const clear = useCallback(() => {
    lifecycle.current.begin();
    if (expiry.current !== null) clearTimeout(expiry.current);
    if (poll.current !== null) clearTimeout(poll.current);
    expiry.current = null;
    poll.current = null;
    setChallenge(null);
    setUsage(null);
    setPacing(null);
    setUsageError(false);
    setMessage('');
  }, []);

  useEffect(() => {
    const start = () => {
      clear();
      if (!isOpen || !uid || document.visibilityState === 'hidden') return;
      const generation = lifecycle.current.begin();
      const current = () =>
        lifecycle.current.current(generation) &&
        document.visibilityState !== 'hidden';
      const tick = async () => {
        if (!current()) return;
        const started = performance.now();
        await Promise.allSettled([
          read('/api/admin/refresh-auth', lifecycle.current.signal)
            .then((raw) => {
              const value = parseRefreshAuth(raw);
              const status = (raw as { pacing?: unknown }).pacing;
              // Pacing is advisory display: a malformed object hides it only.
              if (current()) setPacing(optionalPacing(status));
              return value;
            })
            .then((value) => {
              if (!current()) return;
              setMessage('');
              if (expiry.current !== null) clearTimeout(expiry.current);
              // Subtract all request time: network delay must not extend server expiry.
              const remaining =
                (value.expires_in_seconds ?? 0) * 1000 -
                (performance.now() - started);
              setChallenge(remaining > 0 ? value : null);
              if (remaining > 0)
                expiry.current = setTimeout(() => {
                  if (current()) setChallenge(null);
                }, remaining);
            })
            .catch(() => {
              if (current()) {
                setChallenge(null);
                setPacing(null);
                setMessage(
                  'Duo status unavailable. Check your session or reopen settings.',
                );
              }
            }),
          read('/api/admin/openai-usage', lifecycle.current.signal)
            .then(parseOpenAIUsage)
            .then((value) => {
              if (!current()) return;
              setUsage(value);
              setUsageError(false);
            })
            .catch(() => {
              if (!current()) return;
              setUsage(null);
              setUsageError(true);
            }),
        ]);
        // One pair at a time, bounded to 15s each; no poll while hidden/closed.
        if (current())
          poll.current = setTimeout(() => {
            void tick();
          }, 5000);
      };
      queueMicrotask(() => {
        void tick();
      });
    };
    start();
    document.addEventListener('visibilitychange', start);
    return () => {
      document.removeEventListener('visibilitychange', start);
      clear();
    };
  }, [isOpen, uid, clear]);

  return { challenge, usage, usageError, pacing, message, clear };
}
