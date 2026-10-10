import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import {
  parseRefreshAuth,
  parseRefreshPacing,
  useAdminRefreshStatus,
} from '../../src/lib/useAdminRefreshStatus';

// The backend default policy with no recorded usage (api_models defaults).
const pacing = {
  request_cap: 400,
  daily_request_cap: 1200,
  min_interval_minutes: 30,
  min_gap_seconds: 3,
  max_gap_seconds: 6,
  rest_every_min: 20,
  rest_every_max: 30,
  rest_min_seconds: 45,
  rest_max_seconds: 120,
  used_24h: 0,
  remaining_24h: 1200,
  next_allowed_at: null,
  blocked_until: null,
  last_pacing_stop: null,
  last_error_category: null,
};

const empty = {
  run_id: null,
  approval_required: false,
  code: null,
  expires_in_seconds: 0,
};
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it('does not fetch when closed or hidden, and fences late responses after UID change/close', async () => {
  let finish!: (value: Response) => void;
  const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const { result, rerender } = renderHook(
    ({ open, uid }) => useAdminRefreshStatus(open, uid),
    { initialProps: { open: false, uid: 'admin-a' } },
  );
  await act(async () => {});
  expect(fetch).not.toHaveBeenCalled();
  rerender({ open: true, uid: 'admin-a' });
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  rerender({ open: false, uid: 'admin-b' });
  await act(async () => finish(Response.json(empty)));
  expect(result.current.challenge).toBeNull();
  expect(result.current.usage).toBeNull();
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  rerender({ open: true, uid: 'admin-b' });
  await act(async () => {});
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('clears a random challenge on expiry and tab hiding without overlapping polls', async () => {
  vi.useFakeTimers();
  const code = String(
    crypto.getRandomValues(new Uint32Array(1))[0] % 1000,
  ).padStart(3, '0');
  const challenge = {
    run_id: crypto.randomUUID(),
    approval_required: true,
    code,
    expires_in_seconds: 1,
  };
  const fetch = vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation((path) =>
      String(path).endsWith('refresh-auth')
        ? Promise.resolve(Response.json(challenge))
        : new Promise(() => {}),
    );
  const { result } = renderHook(() => useAdminRefreshStatus(true, 'admin-a'));
  await act(async () => vi.advanceTimersByTimeAsync(0));
  expect(result.current.challenge?.code === code).toBe(true);
  await act(async () => vi.advanceTimersByTimeAsync(1000));
  expect(result.current.challenge).toBeNull();
  await act(async () => vi.advanceTimersByTimeAsync(4000));
  expect(fetch).toHaveBeenCalledTimes(2);
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
  act(() => document.dispatchEvent(new Event('visibilitychange')));
  expect(result.current.challenge).toBeNull();
  expect(result.current.usage).toBeNull();
  await act(async () => vi.advanceTimersByTimeAsync(30_000));
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('rejects malformed challenge metadata without input diagnostics', () => {
  expect(parseRefreshAuth(empty)).toEqual(empty);
  for (const value of [
    { ...empty, code: crypto.randomUUID() },
    { ...empty, expires_in_seconds: 181 },
    { ...empty, extra: true },
  ]) {
    expect(() => parseRefreshAuth(value)).toThrow(
      'Refresh status unavailable.',
    );
  }
});

it('separates the pacing status from the challenge and validates it strictly', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation((path) =>
    String(path).endsWith('refresh-auth')
      ? Promise.resolve(Response.json({ ...empty, pacing }))
      : new Promise(() => {}),
  );
  const { result } = renderHook(() => useAdminRefreshStatus(true, 'admin-a'));
  await waitFor(() => expect(result.current.pacing).toEqual(pacing));
  expect(parseRefreshAuth({ ...empty, pacing })).toEqual(empty);
  const blocked = new Date().toISOString();
  expect(
    parseRefreshPacing({
      ...pacing,
      blocked_until: blocked,
      next_allowed_at: blocked,
      last_pacing_stop: 'ban_risk',
      last_error_category: 'rate_limit',
    }).last_pacing_stop,
  ).toBe('ban_risk');
  for (const value of [
    { ...pacing, used_24h: -1 },
    { ...pacing, request_cap: 1.5 },
    { ...pacing, last_pacing_stop: 'other' },
    { ...pacing, blocked_until: 'not a time' },
    { ...pacing, last_error_category: '<b>' },
    { ...pacing, extra: true },
  ]) {
    expect(() => parseRefreshPacing(value)).toThrow(
      'Refresh status unavailable.',
    );
  }
  act(() => result.current.clear());
  expect(result.current.pacing).toBeNull();
});

it('retains exact Decimal scientific cost strings and unknown subsets without adding tokens', async () => {
  const { parseOpenAIUsage } =
    await import('../../src/lib/useAdminRefreshStatus');
  const counters = {
    default_request_limit: 20,
    default_known_cost_stop_usd: '1',
    pricing_version: '2026-09-09',
    warning: '',
    runs: [
      {
        run_id: crypto.randomUUID(),
        request_limit: 20,
        known_cost_stop_usd: '1',
        attempts: 1,
        unpriced_attempts: 0,
        known_usd: '2E-8',
        total_usd: '2E-8',
        input_tokens: 1,
        output_tokens: 0,
        cached_tokens: 1,
        cache_write_tokens: 0,
        reasoning_tokens: null,
      },
    ],
  };
  expect(parseOpenAIUsage(counters).runs[0]).toEqual(counters.runs[0]);
  counters.runs[0].known_usd = 'NaN';
  expect(() => parseOpenAIUsage(counters)).toThrow('Usage unavailable.');
});

it('separates a usage read failure from a usage read still loading', async () => {
  let fail!: (error: Error) => void;
  vi.spyOn(globalThis, 'fetch').mockImplementation((path) =>
    String(path).endsWith('openai-usage')
      ? new Promise((_, reject) => {
          fail = reject;
        })
      : new Promise(() => {}),
  );
  const { result } = renderHook(() => useAdminRefreshStatus(true, 'admin-a'));
  await waitFor(() => expect(fail).toBeTypeOf('function'));
  expect(result.current.usage).toBeNull();
  expect(result.current.usageError).toBe(false);
  await act(async () => fail(new Error(crypto.randomUUID())));
  await waitFor(() => expect(result.current.usageError).toBe(true));
  act(() => result.current.clear());
  expect(result.current.usageError).toBe(false);
});
