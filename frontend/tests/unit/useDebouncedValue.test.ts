import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useDebouncedValue } from '../../src/lib/useDebouncedValue';

afterEach(() => vi.useRealTimers());
describe('debounced input', () => {
  it('waits 125 ms after the last input and clears immediately', () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ value }) => useDebouncedValue(value, 125),
      { initialProps: { value: '' } },
    );
    rerender({ value: 'a' });
    act(() => vi.advanceTimersByTime(100));
    rerender({ value: 'ab' });
    act(() => vi.advanceTimersByTime(124));
    expect(result.current).toBe('');
    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe('ab');
    rerender({ value: '' });
    expect(result.current).toBe('');
    rerender({ value: 'c' });
    expect(result.current).toBe('');
    act(() => vi.advanceTimersByTime(125));
    expect(result.current).toBe('c');
  });
});
