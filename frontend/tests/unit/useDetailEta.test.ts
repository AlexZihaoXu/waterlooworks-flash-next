import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useDetailEta } from '../../src/lib/useRefreshPresentation';

// Primitive progress counters and a monotonic fake clock only.
afterEach(() => vi.useRealTimers());
const initialProps = {
  run: 'a',
  running: true,
  stage: 'details',
  completed: 50,
  total: 100,
};
function setup() {
  vi.useFakeTimers();
  return renderHook(
    ({ run, running, stage, completed, total }) =>
      useDetailEta(run, running, stage, completed, total),
    { initialProps },
  );
}
it('warms up from a mid-run baseline and includes rests, retries and stalled time', () => {
  const { result, rerender } = setup();
  expect(result.current).toBeNull();
  act(() => vi.advanceTimersByTime(1000));
  rerender({ ...initialProps, completed: 52 });
  expect(result.current).toBeNull();
  act(() => vi.advanceTimersByTime(1000));
  expect(result.current).toBe(48);
  rerender({ ...initialProps, stage: 'detail_rest', completed: 52 });
  act(() => vi.advanceTimersByTime(2000));
  expect(result.current).toBe(96);
  rerender({ ...initialProps, stage: 'detail_retry', completed: 52 });
  act(() => vi.advanceTimersByTime(6000));
  expect(result.current).toBe(240);
  rerender({ ...initialProps, completed: 54 });
  expect(result.current).toBe(115);
  rerender({ ...initialProps, completed: 54 });
  expect(result.current).toBe(115);
});
it('requires two observed completions, clears at completion/terminal and cleans up', () => {
  const { result, rerender, unmount } = setup();
  act(() => vi.advanceTimersByTime(5000));
  rerender({ ...initialProps, completed: 51 });
  expect(result.current).toBeNull();
  rerender({ ...initialProps, completed: 52 });
  expect(result.current).toBe(120);
  rerender({ ...initialProps, completed: 100 });
  expect(result.current).toBeNull();
  rerender({ ...initialProps, running: false, completed: 52 });
  expect(result.current).toBeNull();
  unmount();
  expect(vi.getTimerCount()).toBe(0);
});
it.each(['run', 'regression', 'total', 'phase'] as const)(
  'resets on %s',
  (reset) => {
    const { result, rerender } = setup();
    act(() => vi.advanceTimersByTime(4000));
    rerender({ ...initialProps, completed: 52 });
    expect(result.current).toBe(96);
    const next = { ...initialProps, completed: 52 };
    if (reset === 'run') next.run = 'b';
    if (reset === 'regression') next.completed = 1;
    if (reset === 'total') next.total = 120;
    if (reset === 'phase') {
      rerender({ ...next, stage: 'listings' });
      act(() => vi.advanceTimersByTime(10000));
    }
    rerender(next);
    expect(result.current).toBeNull();
    act(() => vi.advanceTimersByTime(2000));
    rerender({ ...next, completed: next.completed + 2 });
    expect(result.current).toBe(next.total - next.completed - 2);
  },
);
