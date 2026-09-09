import { renderHook, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { useWorkspace } from '../../src/lib/useWorkspace';

it('does not start a doomed workspace request during StrictMode cleanup', async () => {
  // Transport failure only: no fabricated API response or posting data.
  const fetch = vi
    .spyOn(globalThis, 'fetch')
    .mockRejectedValue(new Error('Offline'));
  try {
    const { result, unmount } = renderHook(() => useWorkspace(true), {
      reactStrictMode: true,
    });
    await waitFor(() =>
      expect(result.current.state.status).toBe('disconnected'),
    );
    expect(
      fetch.mock.calls.filter(([url]) => url === '/api/workspace'),
    ).toHaveLength(1);
    unmount();
  } finally {
    fetch.mockRestore();
  }
});
