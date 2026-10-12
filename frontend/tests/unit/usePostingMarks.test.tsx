import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { usePostingMarks } from '../../src/lib/usePostingMarks';

// Generic identifiers and timestamps only; no posting content.
const at = '2026-01-01T00:00:00+00:00';
type Call = { path: string; method: string; body: unknown; headers: unknown };

function fakeServer(
  routes: Record<
    string,
    (body: unknown) => Response | Promise<Response> | object
  >,
): Call[] {
  const calls: Call[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path, method, body, headers: init?.headers });
    const handler = routes[`${method} ${path}`];
    if (!handler) return new Response('{}', { status: 404 });
    const result = await handler(body);
    return result instanceof Response
      ? result
      : new Response(JSON.stringify(result), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

describe('usePostingMarks', () => {
  it('stays idle without transport until enabled', () => {
    const fetch = vi.spyOn(globalThis, 'fetch');
    const { result } = renderHook(() => usePostingMarks(false));
    expect(result.current.status).toBe('idle');
    expect(result.current.marks).toEqual({});
    expect(fetch).not.toHaveBeenCalled();
  });

  it('loads marks once and applies a state optimistically', async () => {
    let release: (value: Response) => void = () => {};
    const calls = fakeServer({
      'GET /api/account/posting-states': () => ({
        marks: {
          a: { posting_id: 'a', state: null, viewed_at: at, updated_at: at },
        },
      }),
      'PUT /api/account/posting-states/b': () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    });
    const { result } = renderHook(() => usePostingMarks(true));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.marks.a?.viewed_at).toBe(at);
    let saved: Promise<boolean> = Promise.resolve(false);
    act(() => {
      saved = result.current.setState('b', 'interested');
    });
    expect(result.current.marks.b?.state).toBe('interested');
    await waitFor(() =>
      expect(calls.some((call) => call.method === 'PUT')).toBe(true),
    );
    const put = calls.find((call) => call.method === 'PUT')!;
    expect(put.body).toEqual({ state: 'interested' });
    expect(put.headers).toEqual({
      'Content-Type': 'application/json',
      'X-WW-Request': '1',
    });
    await act(async () => {
      release(
        new Response(
          JSON.stringify({
            posting_id: 'b',
            state: 'interested',
            viewed_at: null,
            updated_at: at,
          }),
          { status: 200 },
        ),
      );
      expect(await saved).toBe(true);
    });
    expect(result.current.marks.b?.updated_at).toBe(at);
    expect(
      calls.filter((call) => call.path === '/api/account/posting-states'),
    ).toHaveLength(1);
  });

  it('rolls back a failed change without touching other postings', async () => {
    fakeServer({
      'GET /api/account/posting-states': () => ({
        marks: {
          a: {
            posting_id: 'a',
            state: 'applied',
            viewed_at: null,
            updated_at: at,
          },
        },
      }),
      'PUT /api/account/posting-states/a': () =>
        new Response('{}', { status: 500 }),
    });
    const { result } = renderHook(() => usePostingMarks(true));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    let ok = true;
    await act(async () => {
      ok = await result.current.setState('a', null);
    });
    expect(ok).toBe(false);
    expect(result.current.marks.a?.state).toBe('applied');
  });

  it('marks a posting viewed only once and can unmark it', async () => {
    const calls = fakeServer({
      'GET /api/account/posting-states': () => ({ marks: {} }),
      'PUT /api/account/posting-states/a%2Fb/viewed': (body) => ({
        posting_id: 'a/b',
        state: null,
        viewed_at: (body as { viewed: boolean }).viewed ? at : null,
        updated_at: at,
      }),
    });
    const { result } = renderHook(() => usePostingMarks(true));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    await act(async () => {
      result.current.markViewed('a/b');
      result.current.markViewed('a/b');
    });
    await waitFor(() =>
      expect(result.current.marks['a/b']?.viewed_at).toBe(at),
    );
    result.current.markViewed('a/b');
    await act(async () => {
      await result.current.setViewed('a/b', false);
    });
    expect(result.current.marks['a/b']?.viewed_at).toBeNull();
    expect(
      calls.filter((call) => call.method === 'PUT').map((call) => call.body),
    ).toEqual([{ viewed: true }, { viewed: false }]);
  });

  it('reports a failed load and refuses changes it cannot reconcile', async () => {
    const calls = fakeServer({
      'GET /api/account/posting-states': () =>
        new Response('{}', { status: 503 }),
    });
    const { result } = renderHook(() => usePostingMarks(true));
    await waitFor(() => expect(result.current.status).toBe('error'));
    let ok = true;
    await act(async () => {
      ok = await result.current.setState('a', 'applied');
    });
    expect(ok).toBe(false);
    expect(calls.filter((call) => call.method === 'PUT')).toHaveLength(0);
  });

  it('drops marks when disabled, as on sign-out', async () => {
    fakeServer({
      'GET /api/account/posting-states': () => ({
        marks: {
          a: { posting_id: 'a', state: null, viewed_at: at, updated_at: at },
        },
      }),
    });
    const { result, rerender } = renderHook(
      ({ enabled }) => usePostingMarks(enabled),
      { initialProps: { enabled: true } },
    );
    await waitFor(() => expect(result.current.status).toBe('ready'));
    rerender({ enabled: false });
    expect(result.current.status).toBe('idle');
    expect(result.current.marks).toEqual({});
  });
});
