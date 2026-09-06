export function refreshAction(
  available: boolean,
  retryAfter: number,
  running = false,
): 'ignore' | 'running' | 'cooldown' | 'request' {
  if (!available) return 'ignore';
  if (running) return 'running';
  return retryAfter > 0 ? 'cooldown' : 'request';
}
