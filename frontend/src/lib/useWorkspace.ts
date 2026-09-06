import { useCallback, useEffect, useRef, useState } from 'react';
import { refreshAction } from './refreshAction';
import type {
  RefreshSnapshot,
  WorkspaceResponse,
  WorkspaceState,
} from './workspace';

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function validListing(value: unknown): boolean {
  return (
    record(value) &&
    typeof value.id === 'string' &&
    (value.title == null || typeof value.title === 'string') &&
    (value.organization == null || typeof value.organization === 'string')
  );
}

export function parseWorkspace(value: unknown): WorkspaceResponse {
  if (
    !record(value) ||
    value.status !== 'ready' ||
    !Array.isArray(value.postings) ||
    typeof value.manual_refresh_available !== 'boolean' ||
    value.details_available !== false ||
    !value.postings.every(
      (posting) =>
        record(posting) &&
        typeof posting.posting_id === 'string' &&
        typeof posting.first_seen_at === 'string' &&
        typeof posting.last_seen_at === 'string' &&
        (posting.location === null || typeof posting.location === 'string') &&
        posting.country == null &&
        validListing(posting.listing) &&
        Array.isArray(posting.listing_revisions) &&
        posting.listing_revisions.every(
          (revision) =>
            record(revision) &&
            typeof revision.revision_id === 'number' &&
            typeof revision.content_hash === 'string' &&
            typeof revision.observed_at === 'string' &&
            typeof revision.hash_version === 'string' &&
            typeof revision.run_id === 'string' &&
            validListing(revision.snapshot),
        ),
    )
  ) {
    throw new Error('Invalid workspace response.');
  }
  return value as WorkspaceResponse;
}

export function parseRefresh(value: unknown): RefreshSnapshot {
  if (
    !record(value) ||
    !['idle', 'running', 'completed', 'failed', 'interrupted'].includes(
      String(value.status),
    ) ||
    typeof value.stage !== 'string' ||
    (value.run_id != null && typeof value.run_id !== 'string') ||
    ![
      'pages_completed',
      'records_received',
      'unique_postings',
      'duplicates',
      'retry_after',
    ].every(
      (key) =>
        typeof value[key] === 'number' &&
        Number.isInteger(value[key]) &&
        value[key] >= 0,
    ) ||
    (value.total_pages != null &&
      (typeof value.total_pages !== 'number' || value.total_pages < 1)) ||
    (value.eta_seconds != null &&
      (typeof value.eta_seconds !== 'number' ||
        !Number.isSafeInteger(value.eta_seconds) ||
        value.eta_seconds < 0)) ||
    !Array.isArray(value.logs) ||
    value.logs.length > 200 ||
    !value.logs.every(
      (log) =>
        record(log) &&
        typeof log.sequence === 'number' &&
        typeof log.at === 'string' &&
        typeof log.message === 'string' &&
        ['debug', 'info', 'warning', 'error', 'critical'].includes(
          String(log.level),
        ),
    )
  ) {
    throw new Error('Invalid refresh event.');
  }
  return value as RefreshSnapshot;
}

async function readJson(path: string, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(path, { signal, cache: 'no-store' });
  if (
    !response.ok ||
    !response.headers.get('content-type')?.includes('application/json')
  ) {
    throw new Error('API unavailable.');
  }
  return response.json() as Promise<unknown>;
}

export function useWorkspace(enabled: boolean) {
  const [state, setState] = useState<WorkspaceState>({ status: 'loading' });
  const [refresh, setRefresh] = useState<RefreshSnapshot | null>(null);
  const [streamConnected, setStreamConnected] = useState(false);
  const [streamInterrupted, setStreamInterrupted] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const requestInFlight = useRef(false);
  const completedRun = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let events: EventSource | undefined;
    let active = true;
    const loadWorkspace = async () => {
      const value = parseWorkspace(
        await readJson('/api/workspace', controller.signal),
      );
      if (active) setState(value);
    };
    const start = async () => {
      try {
        const [, latest] = await Promise.all([
          loadWorkspace(),
          readJson('/api/refresh', controller.signal),
        ]);
        const snapshot = parseRefresh(latest);
        if (!active) return;
        setRefresh(snapshot);
        completedRun.current =
          snapshot.status === 'completed' ? (snapshot.run_id ?? null) : null;
        events = new EventSource('/api/refresh/events');
        events.onopen = () => {
          if (active) {
            setStreamConnected(true);
            setStreamInterrupted(false);
          }
        };
        events.onerror = () => {
          if (active) {
            setStreamConnected(false);
            setStreamInterrupted(true);
          }
        };
        events.addEventListener('refresh', (event) => {
          if (!active) return;
          try {
            const update = parseRefresh(
              JSON.parse((event as MessageEvent<string>).data),
            );
            setRefresh(update);
            setStreamConnected(true);
            setStreamInterrupted(false);
            if (
              update.status === 'completed' &&
              update.run_id &&
              completedRun.current !== update.run_id
            ) {
              completedRun.current = update.run_id;
              void loadWorkspace().catch(() => {
                if (active) setState({ status: 'error' });
              });
            }
          } catch {
            setStreamConnected(false);
            setStreamInterrupted(true);
            setActionError(
              'Invalid progress event; refresh controls are disabled.',
            );
            events?.close();
          }
        });
      } catch {
        if (active) setState({ status: 'disconnected' });
      }
    };
    void start();
    return () => {
      active = false;
      controller.abort();
      events?.close();
    };
  }, [enabled]);

  const manualRefresh = useCallback(async () => {
    const decision = refreshAction(
      !requestInFlight.current &&
        streamConnected &&
        state.status === 'ready' &&
        state.manual_refresh_available,
      refresh?.retry_after ?? 0,
      refresh?.status === 'running',
    );
    if (decision !== 'request') return decision;
    requestInFlight.current = true;
    setRequesting(true);
    setActionError(null);
    try {
      const response = await fetch('/api/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
        cache: 'no-store',
      });
      if (![202, 409, 429].includes(response.status)) {
        setActionError('Refresh was not accepted. No automatic retry.');
      }
      // Read authoritative status; never guess success or automatically retry POST.
      setRefresh(parseRefresh(await readJson('/api/refresh')));
      if (response.status === 409) return 'running';
      if (response.status === 429) return 'cooldown';
    } catch {
      setActionError(
        'Refresh status is uncertain. Check the live stream before trying again.',
      );
      setStreamConnected(false);
    } finally {
      requestInFlight.current = false;
      setRequesting(false);
    }
  }, [streamConnected, state, refresh]);

  return {
    state,
    refresh,
    streamConnected,
    streamInterrupted,
    requesting,
    actionError,
    manualRefresh,
  };
}
