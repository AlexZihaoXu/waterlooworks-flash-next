import { useEffect, useState } from 'react';
import { tickDelay } from './refreshSchedule';

/**
 * Current time for relative labels: re-renders every second while any value
 * is under a minute away, otherwise about every 30 s, and immediately when the
 * tab becomes visible again (timers are throttled while hidden).
 */
export function useRelativeNow(
  values: readonly (string | null | undefined)[],
): number {
  const [now, setNow] = useState(() => Date.now());
  const key = values.join('|');
  useEffect(() => {
    const timer = window.setTimeout(
      () => setNow(Date.now()),
      tickDelay(key.split('|'), Date.now()),
    );
    const visible = () => {
      if (document.visibilityState === 'visible') setNow(Date.now());
    };
    document.addEventListener('visibilitychange', visible);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [key, now]);
  return now;
}
