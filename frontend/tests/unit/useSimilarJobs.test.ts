import { renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useSimilarJobs } from '../../src/lib/useSimilarJobs';
import type { DetailState } from '../../src/lib/useJobDetail';

afterEach(() => vi.unstubAllGlobals());
it('does not start workers or requests without a ready saved description', () => {
  const worker = vi.fn();
  const transport = vi.fn();
  vi.stubGlobal('Worker', worker);
  vi.stubGlobal('fetch', transport);
  const { result, rerender } = renderHook(
    ({ detail }: { detail: DetailState | null }) => useSimilarJobs(detail, ''),
    { initialProps: { detail: null as DetailState | null } },
  );
  expect(result.current.status).toBe('idle');
  rerender({ detail: { id: '0', status: 'loading' } });
  rerender({ detail: { id: '0', status: 'not-found' } });
  expect(result.current.status).toBe('idle');
  expect(worker).not.toHaveBeenCalled();
  expect(transport).not.toHaveBeenCalled();
});
