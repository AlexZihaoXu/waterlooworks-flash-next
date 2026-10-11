import { parseRefreshPacing } from './useAdminRefreshStatus';

/** Fixed header on the confirmable 409: the previous run's finish (UTC ISO). */
export const RECENT_HEADER = 'X-WW-Refresh-Recent';

function isoTime(value: unknown): string | null {
  return typeof value === 'string' &&
    value.length <= 64 &&
    !Number.isNaN(Date.parse(value))
    ? value
    : null;
}

/**
 * The previous finish while the minimum interval is still running, read from
 * the admin pacing status just before a refresh; null otherwise or on any
 * failure (the server still answers 409 with RECENT_HEADER).
 */
export async function readRecentRefresh(
  signal: AbortSignal,
): Promise<string | null> {
  signal.throwIfAborted();
  try {
    const response = await fetch('/api/admin/refresh-auth', {
      signal,
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
    });
    if (!response.ok) return null;
    const raw: unknown = await response.json();
    const pacing = parseRefreshPacing(
      raw && typeof raw === 'object'
        ? (raw as { pacing?: unknown }).pacing
        : undefined,
    );
    const until = pacing.cooldown_until
      ? Date.parse(pacing.cooldown_until)
      : Number.NaN;
    return until > Date.now() ? isoTime(pacing.last_finished_at) : null;
  } catch {
    signal.throwIfAborted();
    return null;
  }
}

// Cancellation stops this client's continuation, not collection already accepted
// by the server. Every request and result belongs to the same workspace mount.
export async function readManualRefresh(
  signal: AbortSignal,
  confirmRecent = false,
) {
  signal.throwIfAborted();
  const response = await fetch('/api/refresh', {
    signal,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-WW-Request': '1' },
    body: confirmRecent ? JSON.stringify({ confirm_recent: true }) : '{}',
    cache: 'no-store',
  });
  signal.throwIfAborted();
  const recentFinishedAt =
    response.status === 409
      ? isoTime(response.headers.get(RECENT_HEADER))
      : null;
  // Read authoritative status; never guess success or automatically retry POST.
  const latest = await fetch('/api/refresh', { signal, cache: 'no-store' });
  signal.throwIfAborted();
  if (
    !latest.ok ||
    !latest.headers.get('content-type')?.includes('application/json')
  ) {
    throw new Error('API unavailable.');
  }
  const snapshot: unknown = await latest.json();
  signal.throwIfAborted();
  return { status: response.status, snapshot, recentFinishedAt };
}
