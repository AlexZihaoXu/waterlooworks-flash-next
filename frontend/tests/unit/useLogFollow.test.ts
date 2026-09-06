import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { isAtBottom, useLogFollow } from '../../src/lib/useLogFollow';

describe('console follow behavior', () => {
  it('recognizes the bottom, including rounding and non-overflowing content', () => {
    expect(isAtBottom(1000, 200, 800)).toBe(true);
    expect(isAtBottom(1000, 200, 776)).toBe(true);
    expect(isAtBottom(1000, 200, 775)).toBe(false);
    expect(isAtBottom(100, 200, 0)).toBe(true);
  });

  it('follows additions only while at the bottom and resumes on request', () => {
    // JSDOM has no layout engine. These are generic scroll dimensions, not source/API fixtures.
    const node = document.createElement('div');
    let height = 1000;
    let top = 0;
    Object.defineProperties(node, {
      scrollHeight: { get: () => height },
      clientHeight: { get: () => 200 },
      scrollTop: {
        get: () => top,
        set: (value: number) => {
          top = Math.max(0, Math.min(value, height - 200));
        },
      },
    });
    const { result, rerender } = renderHook(
      ({ open, revision }) => useLogFollow(open, revision),
      {
        initialProps: { open: false, revision: '1' },
      },
    );
    result.current.ref.current = node;
    rerender({ open: true, revision: '1' });
    expect(top).toBe(800);
    height = 1200;
    rerender({ open: true, revision: '2' });
    expect(top).toBe(1000);
    act(() => {
      node.scrollTop = 100;
      result.current.onScroll();
    });
    expect(result.current.following).toBe(false);
    height = 1400;
    rerender({ open: true, revision: '3' });
    expect(top).toBe(100);
    rerender({ open: false, revision: '3' });
    rerender({ open: true, revision: '3' });
    expect(top).toBe(100);
    act(() => result.current.jumpToLatest());
    expect(top).toBe(1200);
    expect(result.current.following).toBe(true);
    act(() => {
      node.scrollTop = 400;
      result.current.onScroll();
    });
    act(() => {
      node.scrollTop = 1200;
      result.current.onScroll();
    });
    height = 1600;
    rerender({ open: true, revision: '4' });
    expect(top).toBe(1400);
  });
});
