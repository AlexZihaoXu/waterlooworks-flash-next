import { act, renderHook } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { useLiveReducedMotion } from '../../src/lib/useLiveReducedMotion';

it('responds to OS preference changes while mounted', () => {
  // OS media state only, not API/posting data.
  const media = Object.assign(new EventTarget(), { matches: false });
  const matchMedia = vi
    .spyOn(window, 'matchMedia')
    .mockReturnValue(media as MediaQueryList);
  try {
    const { result, unmount } = renderHook(useLiveReducedMotion);
    expect(result.current).toBe(false);
    act(() => {
      media.matches = true;
      media.dispatchEvent(new Event('change'));
    });
    expect(result.current).toBe(true);
    act(() => {
      media.matches = false;
      media.dispatchEvent(new Event('change'));
    });
    expect(result.current).toBe(false);
    unmount();
  } finally {
    matchMedia.mockRestore();
  }
});
