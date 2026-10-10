import type { RefreshSnapshot } from './workspace';

// After a crash the backend restores the persisted 'running' row with stage
// 'recovery_required'. That run has no live worker and must never be shown
// as an active refresh.
export function refreshRunning(refresh: RefreshSnapshot | null): boolean {
  return refresh?.status === 'running' && refresh.stage !== 'recovery_required';
}

export function refreshInterruption(
  refresh: RefreshSnapshot | null,
): 'recovery_required' | 'interrupted' | null {
  if (!refresh) return null;
  if (refresh.stage === 'recovery_required') return 'recovery_required';
  return refresh.status === 'interrupted' ? 'interrupted' : null;
}
