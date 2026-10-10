import { useSyncExternalStore } from 'react';

function subscribe(listener: () => void) {
  window.addEventListener('scroll', listener, { passive: true });
  return () => window.removeEventListener('scroll', listener);
}

/** Whether the page has scrolled at all; changes only at the threshold. */
export function useWindowScrolled(enabled = true): boolean {
  return useSyncExternalStore(
    subscribe,
    () => enabled && window.scrollY > 0,
    () => false,
  );
}
