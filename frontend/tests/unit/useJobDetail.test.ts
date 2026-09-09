import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useJobDetail } from '../../src/lib/useJobDetail';
import { fetchJobDetail, parseJobDetail } from '../../src/lib/jobDetail';

afterEach(() => vi.unstubAllGlobals());
// Transport rejection/deferred transport only: no fabricated API responses.
it('aborts superseded requests, suppresses stale errors and never polls or retries', async () => {
  const rejectors: ((reason: Error) => void)[] = [];
  const transport = vi.fn<
    (url: string, options: RequestInit) => Promise<Response>
  >(
    () =>
      new Promise<Response>((_resolve, reject) => {
        rejectors.push(reject);
      }),
  );
  vi.stubGlobal('fetch', transport);
  const { result, rerender, unmount } = renderHook(
    ({ id }) => useJobDetail(id),
    { initialProps: { id: '0012' } },
  );
  expect(result.current?.status).toBe('loading');
  const firstOptions = transport.mock.calls[0][1];
  expect(transport.mock.calls[0][0]).toBe('/api/postings/0012');
  expect(firstOptions.cache).toBe('no-store');
  expect(firstOptions.method).toBeUndefined();
  rerender({ id: '0013' });
  expect(firstOptions.signal?.aborted).toBe(true);
  await act(async () => rejectors[0](new Error('Transport cancelled')));
  expect(result.current).toEqual({ id: '0013', status: 'loading' });
  await act(async () => rejectors[1](new Error('Offline')));
  await waitFor(() => expect(result.current?.status).toBe('error'));
  expect(transport).toHaveBeenCalledTimes(2);
  unmount();
  expect(transport.mock.calls[1][1].signal?.aborted).toBe(true);
});
it('reloads a selected posting once when refresh completion changes its revision token', async () => {
  const transport = vi.fn(() => Promise.reject(new Error('Offline')));
  vi.stubGlobal('fetch', transport);
  const { result, rerender } = renderHook(
    ({ revision }) => useJobDetail('0012', revision),
    { initialProps: { revision: 'run:running' } },
  );
  await waitFor(() => expect(result.current?.status).toBe('error'));
  rerender({ revision: 'run:completed' });
  await waitFor(() => expect(transport).toHaveBeenCalledTimes(2));
  rerender({ revision: 'run:completed' });
  expect(transport).toHaveBeenCalledTimes(2);
});

it('rejects invalid IDs before transport and invalid response primitives', async () => {
  const transport = vi.fn();
  vi.stubGlobal('fetch', transport);
  await expect(
    fetchJobDetail('invalid', new AbortController().signal),
  ).rejects.toThrow('Invalid posting ID');
  expect(transport).not.toHaveBeenCalled();
  for (const value of [null, [], '', 1, {}])
    expect(() => parseJobDetail(value, '0')).toThrow(
      'Invalid stored posting response',
    );
});
