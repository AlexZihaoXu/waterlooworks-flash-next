import { useCallback, useEffect, useRef, useState } from 'react';
import { refreshAction } from './refreshAction';
import { readManualRefresh, readRecentRefresh } from './workspaceRefresh';
import {
  createLatestWorkspaceRead,
  createWorkspaceReloadPolicy,
} from './workspaceReload';
import type {
  RefreshSnapshot,
  WorkspaceResponse,
  WorkspaceState,
} from './workspace';

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function validCompensation(value: unknown): boolean {
  if (value == null) return true;
  if (
    !record(value) ||
    (value.currency !== null &&
      (typeof value.currency !== 'string' || !value.currency.trim())) ||
    (value.period !== null &&
      !['hour', 'day', 'week', 'month', 'year'].includes(
        String(value.period),
      )) ||
    !['estimated', 'assumed_hours', 'annualized'].every(
      (key) => value[key] === undefined || typeof value[key] === 'boolean',
    ) ||
    (value.hours_per_week !== undefined &&
      (!Array.isArray(value.hours_per_week) ||
        !value.hours_per_week.every(
          (hours) =>
            typeof hours === 'string' &&
            /^\d+(?:\.\d+)?$/.test(hours) &&
            Number(hours) > 0,
        )))
  )
    return false;
  return ['minimum', 'maximum', 'average'].every(
    (key) =>
      value[key] === null ||
      (typeof value[key] === 'string' && /^\d+(?:\.\d+)?$/.test(value[key])),
  );
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
    (value.compensation_sort_available !== undefined &&
      typeof value.compensation_sort_available !== 'boolean') ||
    value.details_available !== false ||
    !value.postings.every(
      (posting) =>
        record(posting) &&
        typeof posting.posting_id === 'string' &&
        typeof posting.first_seen_at === 'string' &&
        typeof posting.last_seen_at === 'string' &&
        (posting.location === null || typeof posting.location === 'string') &&
        posting.country == null &&
        (posting.targeted_disciplines === undefined ||
          (Array.isArray(posting.targeted_disciplines) &&
            posting.targeted_disciplines.every(
              (value) => typeof value === 'string',
            ))) &&
        validCompensation(posting.compensation_sort) &&
        validListing(posting.listing),
    )
  ) {
    throw new Error('Invalid workspace response.');
  }
  return {
    ...value,
    compensation_sort_available: value.compensation_sort_available ?? false,
    postings: value.postings.map((posting) => ({
      ...posting,
      targeted_disciplines: posting.targeted_disciplines ?? [],
      compensation_sort: posting.compensation_sort ?? null,
    })),
  } as WorkspaceResponse;
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
      'details_completed',
      'details_total',
      'detail_requests',
      'detail_retries',
      'detail_bursts',
    ].every(
      (key) =>
        typeof value[key] === 'number' &&
        Number.isInteger(value[key]) &&
        value[key] >= 0,
    ) ||
    typeof value.detail_limited !== 'boolean' ||
    // Pacing fields are optional on read; when present they must be exact.
    (value.ww_requests !== undefined &&
      (typeof value.ww_requests !== 'number' ||
        !Number.isSafeInteger(value.ww_requests) ||
        value.ww_requests < 0)) ||
    (value.ww_request_budget != null &&
      (typeof value.ww_request_budget !== 'number' ||
        !Number.isSafeInteger(value.ww_request_budget) ||
        value.ww_request_budget < 0)) ||
    (value.pacing_stop != null &&
      !['request_cap', 'daily_cap', 'ban_risk'].includes(
        String(value.pacing_stop),
      )) ||
    (value.error_category != null &&
      (typeof value.error_category !== 'string' ||
        !/^[a-z_]{1,40}$/.test(value.error_category))) ||
    typeof value.detail_wait_seconds !== 'number' ||
    !Number.isFinite(value.detail_wait_seconds) ||
    value.detail_wait_seconds < 0 ||
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

// EventSource reconnects by itself (the server asks for a 1-second retry); a
// session renewal rotates the cookie and ends the old stream every hour, so
// only a stream that stays down this long is reported as interrupted.
export const STREAM_INTERRUPTED_GRACE_MS = 5_000;

export type ManualRefreshResult =
  ReturnType<typeof refreshAction> | { recent: string } | undefined;

export function useWorkspace(enabled: boolean) {
  const [state, setState] = useState<WorkspaceState>({ status: 'loading' });
  const [refresh, setRefresh] = useState<RefreshSnapshot | null>(null);
  const [streamConnected, setStreamConnected] = useState(false);
  const [streamInterrupted, setStreamInterrupted] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const requestInFlight = useRef(false);
  const mountController = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    mountController.current = controller;
    let events: EventSource | undefined;
    let interruption: ReturnType<typeof setTimeout> | undefined;
    let active = true;
    const reconnected = () => {
      clearTimeout(interruption);
      interruption = undefined;
      setStreamConnected(true);
      setStreamInterrupted(false);
    };
    const shouldReload = createWorkspaceReloadPolicy();
    const latestRead = createLatestWorkspaceRead();
    const loadWorkspace = (fail: () => void) =>
      latestRead(
        async () =>
          parseWorkspace(await readJson('/api/workspace', controller.signal)),
        (value) => {
          if (active) setState(value);
        },
        () => {
          if (active) fail();
        },
      );
    const start = async () => {
      if (!active) return;
      try {
        const [, latest] = await Promise.all([
          loadWorkspace(() => {
            throw new Error('Workspace unavailable.');
          }),
          readJson('/api/refresh', controller.signal),
        ]);
        const snapshot = parseRefresh(latest);
        if (!active) return;
        setRefresh(snapshot);
        events = new EventSource('/api/refresh/events');
        events.onopen = () => {
          if (active) reconnected();
        };
        events.onerror = () => {
          if (!active) return;
          // Refresh controls stay disabled at once; the warning waits.
          setStreamConnected(false);
          interruption ??= setTimeout(() => {
            interruption = undefined;
            if (active) setStreamInterrupted(true);
          }, STREAM_INTERRUPTED_GRACE_MS);
        };
        events.addEventListener('refresh', (event) => {
          if (!active) return;
          try {
            const update = parseRefresh(
              JSON.parse((event as MessageEvent<string>).data),
            );
            setRefresh(update);
            reconnected();
            if (shouldReload(update.run_id, update.status, update.stage)) {
              // Show saved listings early, then include saved detail/filter values
              // once at termination, even when only partial details were saved.
              void loadWorkspace(() => setState({ status: 'error' }));
            }
          } catch {
            clearTimeout(interruption);
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
    // Skip React's immediately discarded StrictMode effect before sending HTTP.
    void Promise.resolve().then(start);
    return () => {
      active = false;
      controller.abort();
      mountController.current = null;
      requestInFlight.current = false;
      clearTimeout(interruption);
      events?.close();
    };
  }, [enabled]);

  /**
   * Without confirmation, a refresh inside the post-run interval returns
   * ``{ recent }`` (the previous finish) before any POST, so the caller can
   * ask the admin; a stale client gets the same from the server 409 signal.
   */
  const manualRefresh = useCallback(
    async (confirmRecent = false): Promise<ManualRefreshResult> => {
      const controller = mountController.current;
      const current = () =>
        controller !== null &&
        mountController.current === controller &&
        !controller.signal.aborted;
      const decision = refreshAction(
        enabled &&
          current() &&
          !requestInFlight.current &&
          streamConnected &&
          state.status === 'ready' &&
          state.manual_refresh_available,
        refresh?.retry_after ?? 0,
        refresh?.status === 'running',
      );
      if (decision !== 'request' || !controller) return decision;
      requestInFlight.current = true;
      setRequesting(true);
      setActionError(null);
      try {
        if (!confirmRecent) {
          const recent = await readRecentRefresh(controller.signal);
          if (!current()) return;
          if (recent !== null) return { recent };
        }
        const response = await readManualRefresh(
          controller.signal,
          confirmRecent,
        );
        if (!current()) return;
        if (![202, 409, 429].includes(response.status)) {
          setActionError('Refresh was not accepted. No automatic retry.');
        }
        setRefresh(parseRefresh(response.snapshot));
        if (response.recentFinishedAt !== null)
          return { recent: response.recentFinishedAt };
        if (response.status === 409) return 'running';
        if (response.status === 429) return 'paused';
      } catch {
        if (!current()) return;
        setActionError(
          'Refresh status is uncertain. Check the live stream before trying again.',
        );
        setStreamConnected(false);
      } finally {
        if (current()) {
          requestInFlight.current = false;
          setRequesting(false);
        }
      }
    },
    [enabled, streamConnected, state, refresh],
  );

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
