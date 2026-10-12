import { useCallback, useEffect, useRef, useState } from 'react';
import { AuthGeneration, runAuthOperation } from './authLifecycle';
import {
  applyMarkState,
  applyViewed,
  parsePostingMark,
  parsePostingMarks,
  type MarkState,
  type PostingMark,
  type PostingMarks,
} from './postingMarks';

export type MarksStatus = 'idle' | 'loading' | 'ready' | 'error';
const endpoint = '/api/account/posting-states';
const NO_MARKS: PostingMarks = {};

function request(
  path: string,
  signal: AbortSignal,
  body?: object,
): Promise<unknown> {
  return runAuthOperation(async (operation) => {
    const response = await operation.wait(
      fetch(path, {
        method: body ? 'PUT' : 'GET',
        body: body ? JSON.stringify(body) : undefined,
        headers: body
          ? { 'Content-Type': 'application/json', 'X-WW-Request': '1' }
          : undefined,
        credentials: 'same-origin',
        cache: 'no-store',
        signal: operation.signal,
      }),
    );
    if (!response.ok) throw new Error('Marks unavailable.');
    return operation.wait(response.json());
  }, signal);
}

/**
 * The signed-in user's posting marks: one load per session, optimistic
 * changes with per-posting rollback. The App is keyed by account, and
 * disabling (sign-out) or unmounting cancels every pending response.
 */
export function usePostingMarks(enabled: boolean) {
  const lifecycle = useRef(new AuthGeneration());
  const marksRef = useRef<PostingMarks>(NO_MARKS);
  const statusRef = useRef<MarksStatus>('idle');
  const versions = useRef(new Map<string, number>());
  const viewing = useRef(new Set<string>());
  const [marks, setMarks] = useState<PostingMarks>(NO_MARKS);
  const [status, setStatusState] = useState<MarksStatus>('idle');

  const commit = useCallback((next: PostingMarks) => {
    marksRef.current = next;
    setMarks(next);
  }, []);
  const setStatus = useCallback((next: MarksStatus) => {
    statusRef.current = next;
    setStatusState(next);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    const lifetime = lifecycle.current;
    const pending = versions.current;
    const inFlight = viewing.current;
    const generation = lifetime.begin();
    // Deferred so a StrictMode cleanup never starts a doomed request.
    queueMicrotask(() => {
      if (!lifetime.current(generation)) return;
      setStatus('loading');
      void request(endpoint, lifetime.signal)
        .then(parsePostingMarks)
        .then((loaded) => {
          if (!lifetime.current(generation)) return;
          commit(loaded);
          setStatus('ready');
        })
        .catch(() => {
          if (lifetime.current(generation)) setStatus('error');
        });
    });
    // Sign-out or account change: cancel responses and forget every mark.
    return () => {
      lifetime.begin();
      pending.clear();
      inFlight.clear();
      commit(NO_MARKS);
      setStatus('idle');
    };
  }, [enabled, commit, setStatus]);

  const mutate = useCallback(
    async (
      id: string,
      path: string,
      body: object,
      next: (mark: PostingMark | undefined) => PostingMark,
    ): Promise<boolean> => {
      if (statusRef.current !== 'ready') return false;
      const lifetime = lifecycle.current;
      const signal = lifetime.signal;
      const previous = marksRef.current[id];
      const version = (versions.current.get(id) ?? 0) + 1;
      versions.current.set(id, version);
      commit({ ...marksRef.current, [id]: next(previous) });
      const latest = () =>
        !signal.aborted && versions.current.get(id) === version;
      try {
        const saved = parsePostingMark(await request(path, signal, body));
        if (saved.posting_id !== id) throw new Error('Marks unavailable.');
        if (latest()) commit({ ...marksRef.current, [id]: saved });
        return !signal.aborted;
      } catch {
        if (latest()) {
          const rest = { ...marksRef.current };
          if (previous) rest[id] = previous;
          else delete rest[id];
          commit(rest);
        }
        return false;
      }
    },
    [commit],
  );

  const setState = useCallback(
    (id: string, state: MarkState | null) =>
      mutate(id, `${endpoint}/${encodeURIComponent(id)}`, { state }, (mark) =>
        applyMarkState(mark, id, state, new Date().toISOString()),
      ),
    [mutate],
  );

  const setViewed = useCallback(
    (id: string, viewed: boolean) =>
      mutate(
        id,
        `${endpoint}/${encodeURIComponent(id)}/viewed`,
        { viewed },
        (mark) => applyViewed(mark, id, viewed, new Date().toISOString()),
      ),
    [mutate],
  );

  /** Automatic first-view mark: never re-sent while viewed or in flight. */
  const markViewed = useCallback(
    (id: string) => {
      if (
        statusRef.current !== 'ready' ||
        marksRef.current[id]?.viewed_at ||
        viewing.current.has(id)
      )
        return;
      viewing.current.add(id);
      void setViewed(id, true).finally(() => viewing.current.delete(id));
    },
    [setViewed],
  );

  return { status, marks, setState, setViewed, markViewed };
}

export type PostingMarksApi = ReturnType<typeof usePostingMarks>;
