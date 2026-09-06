import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  useRefreshEta,
  useRefreshLogVisibility,
} from '../../src/lib/useRefreshPresentation';

// Primitive timer/control inputs only; no fabricated refresh responses or postings.
afterEach(() => vi.useRealTimers());
describe('refresh presentation timing', () => {
  it('ticks every 1200ms, accepts revised estimates and expires without implying completion', () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ run, running, estimate }) => useRefreshEta(run, running, estimate),
      {
        initialProps: {
          run: 'a',
          running: true,
          estimate: 10 as number | null,
        },
      },
    );
    act(() => vi.advanceTimersByTime(1199));
    expect(result.current).toBe(10);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe(9);
    rerender({ run: 'a', running: true, estimate: 7 });
    expect(result.current).toBe(7);
    rerender({ run: 'a', running: true, estimate: 20 });
    expect(result.current).toBe(20);
    act(() => vi.advanceTimersByTime(1200));
    expect(result.current).toBe(19);
    act(() => vi.advanceTimersByTime(60000));
    expect(result.current).toBe(0);
    rerender({ run: 'a', running: true, estimate: null });
    expect(result.current).toBeNull();
    act(() => vi.advanceTimersByTime(1200));
    expect(result.current).toBeNull();
    rerender({ run: 'a', running: true, estimate: 5 });
    expect(result.current).toBe(5);
    rerender({ run: 'a', running: false, estimate: 5 });
    expect(result.current).toBeNull();
    rerender({ run: 'b', running: true, estimate: 30 });
    expect(result.current).toBe(30);
  });
  it('anchors the next tick to a fresh estimate', () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ estimate }) => useRefreshEta('a', true, estimate),
      { initialProps: { estimate: 10 } },
    );
    act(() => vi.advanceTimersByTime(1000));
    rerender({ estimate: 8 });
    act(() => vi.advanceTimersByTime(200));
    expect(result.current).toBe(8);
    act(() => vi.advanceTimersByTime(1000));
    expect(result.current).toBe(7);
  });
  it('resynchronizes identical estimates from new samples, not unrelated renders', () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ sample }) => useRefreshEta('a', true, 10, sample),
      { initialProps: { sample: 1 } },
    );
    act(() => vi.advanceTimersByTime(2400));
    expect(result.current).toBe(8);
    rerender({ sample: 1 });
    expect(result.current).toBe(8);
    rerender({ sample: 2 });
    expect(result.current).toBe(10);
  });
  it('hides old terminal runs and grants an observed run exactly three seconds', () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ run, running }) => useRefreshLogVisibility(run, running),
      { initialProps: { run: 'a', running: false } },
    );
    expect(result.current).toBe(false);
    rerender({ run: 'b', running: true });
    expect(result.current).toBe(true);
    rerender({ run: 'b', running: false });
    act(() => vi.advanceTimersByTime(2000));
    expect(result.current).toBe(true);
    rerender({ run: 'b', running: false });
    act(() => vi.advanceTimersByTime(999));
    expect(result.current).toBe(true);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe(false);
  });
  it('cancels an old hide timer when a new run starts and cleans up on unmount', () => {
    vi.useFakeTimers();
    const { result, rerender, unmount } = renderHook(
      ({ run, running }) => useRefreshLogVisibility(run, running),
      { initialProps: { run: 'a', running: true } },
    );
    rerender({ run: 'a', running: false });
    act(() => vi.advanceTimersByTime(2000));
    rerender({ run: 'b', running: true });
    act(() => vi.advanceTimersByTime(2000));
    expect(result.current).toBe(true);
    rerender({ run: 'b', running: false });
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
