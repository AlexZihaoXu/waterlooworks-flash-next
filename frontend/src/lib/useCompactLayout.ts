import { useSyncExternalStore } from 'react';

/** Phone layout breakpoint shared by the workspace, refresh status and menus. */
export const COMPACT_MAX_WIDTH = 640;

function subscribe(listener: () => void) {
  window.addEventListener('resize', listener);
  return () => window.removeEventListener('resize', listener);
}

export function useCompactLayout(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.innerWidth <= COMPACT_MAX_WIDTH,
    () => false,
  );
}
