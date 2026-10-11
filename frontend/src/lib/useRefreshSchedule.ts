import { useCallback, useEffect, useRef, useState } from 'react';
import { AuthGeneration, runAuthOperation } from './authLifecycle';
import { parseScheduleView, type RefreshScheduleView } from './refreshSchedule';

const endpoint = '/api/admin/refresh-schedule';
const UNAVAILABLE =
  'Schedule unavailable. Check your session or reopen settings.';

function call(
  path: string,
  signal: AbortSignal,
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' = 'GET',
  body?: object,
): Promise<RefreshScheduleView> {
  return runAuthOperation(async (operation) => {
    const response = await operation.wait(
      fetch(path, {
        method,
        body: method === 'GET' ? undefined : JSON.stringify(body ?? {}),
        headers:
          method === 'GET'
            ? undefined
            : { 'Content-Type': 'application/json', 'X-WW-Request': '1' },
        credentials: 'same-origin',
        cache: 'no-store',
        signal: operation.signal,
      }),
    );
    if (!response.ok) {
      // The backend's fixed, input-free validation messages are shown as-is.
      const detail = await operation
        .wait(response.json())
        .catch(() => null as unknown);
      throw new Error(
        response.status === 422 || response.status === 404
          ? detail &&
            typeof detail === 'object' &&
            'detail' in detail &&
            typeof detail.detail === 'string' &&
            detail.detail.length <= 200
            ? detail.detail
            : UNAVAILABLE
          : UNAVAILABLE,
      );
    }
    return parseScheduleView(await operation.wait(response.json()));
  }, signal);
}

/** Admin-only schedule settings; tab memory only, one generation per action. */
export function useRefreshSchedule(isOpen: boolean, uid: string) {
  const lifecycle = useRef(new AuthGeneration());
  const [view, setView] = useState<RefreshScheduleView | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  const clear = useCallback(() => {
    lifecycle.current.begin();
    setView(null);
    setPending(false);
    setError('');
  }, []);

  const run = useCallback(
    async (method: 'GET' | 'POST' | 'PUT' | 'DELETE', body?: object) => {
      const generation = lifecycle.current.begin();
      setPending(true);
      if (method !== 'GET') setError('');
      try {
        const next = await call(
          method === 'PUT' ? `${endpoint}/enabled` : endpoint,
          lifecycle.current.signal,
          method,
          body,
        );
        if (!lifecycle.current.current(generation)) return false;
        setView(next);
        setError('');
        return true;
      } catch (failure) {
        if (lifecycle.current.current(generation))
          setError(failure instanceof Error ? failure.message : UNAVAILABLE);
        return false;
      } finally {
        if (lifecycle.current.current(generation)) setPending(false);
      }
    },
    [],
  );

  useEffect(() => {
    const start = () => {
      clear();
      if (isOpen && uid)
        queueMicrotask(() => {
          void run('GET');
        });
    };
    start();
    return clear;
  }, [isOpen, uid, clear, run]);

  return {
    view,
    pending,
    error,
    add: (time: string) => run('POST', { time }),
    remove: (time: string) => run('DELETE', { time }),
    setEnabled: (enabled: boolean) => run('PUT', { enabled }),
  };
}
