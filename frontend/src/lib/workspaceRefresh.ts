// Cancellation stops this client's continuation, not collection already accepted
// by the server. Every request and result belongs to the same workspace mount.
export async function readManualRefresh(signal: AbortSignal) {
  signal.throwIfAborted();
  const response = await fetch('/api/refresh', {
    signal,
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-WW-Request': '1' },
    body: '{}',
    cache: 'no-store',
  });
  signal.throwIfAborted();
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
  return { status: response.status, snapshot };
}
