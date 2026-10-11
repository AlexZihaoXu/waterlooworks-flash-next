export function refreshAction(
  available: boolean,
  retryAfter: number,
  running = false,
): 'ignore' | 'running' | 'paused' | 'request' {
  if (!available) return 'ignore';
  if (running) return 'running';
  // retry_after covers hard waits only (block, rolling budget); the post-run
  // interval is confirmed in a dialog instead.
  return retryAfter > 0 ? 'paused' : 'request';
}
