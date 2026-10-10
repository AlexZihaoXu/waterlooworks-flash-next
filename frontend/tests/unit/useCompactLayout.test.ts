import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useCompactLayout } from '../../src/lib/useCompactLayout';

afterEach(() => vi.unstubAllGlobals());

it('reports phone widths up to 640px and follows window resizes', () => {
  vi.stubGlobal('innerWidth', 390);
  const { result } = renderHook(() => useCompactLayout());
  expect(result.current).toBe(true);
  act(() => {
    vi.stubGlobal('innerWidth', 1024);
    window.dispatchEvent(new Event('resize'));
  });
  expect(result.current).toBe(false);
  act(() => {
    vi.stubGlobal('innerWidth', 640);
    window.dispatchEvent(new Event('resize'));
  });
  expect(result.current).toBe(true);
});
