import { useEffect, useState } from 'react';
import { parseAccountProfile, type AccountProfile } from './accountProfile';

// Optional and separate from auth/refresh: no tokens, persistence, shared cache or retries.
export function useAccountProfile(uid: string | null): AccountProfile | null {
  const [loaded, setLoaded] = useState<{
    uid: string;
    profile: AccountProfile;
  } | null>(null);
  useEffect(() => {
    if (!uid) return;
    const controller = new AbortController();
    const signal = AbortSignal.any([
      controller.signal,
      AbortSignal.timeout(15_000),
    ]);
    void Promise.resolve().then(async () => {
      if (signal.aborted) return;
      try {
        const response = await fetch('/api/auth/profile', {
          credentials: 'same-origin',
          cache: 'no-store',
          signal,
        });
        if (!response.ok) return;
        const profile = parseAccountProfile(await response.json());
        if (!signal.aborted) setLoaded({ uid, profile });
      } catch {
        // A missing profile never hides the workspace or changes authorization.
      }
    });
    return () => {
      controller.abort();
      setLoaded(null);
    };
  }, [uid]);
  return loaded?.uid === uid ? loaded.profile : null;
}
