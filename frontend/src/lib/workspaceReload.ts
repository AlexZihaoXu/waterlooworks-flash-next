// Per-effect history also deduplicates replayed events after SSE reconnects.
export function createWorkspaceReloadPolicy() {
  const loaded = new Map<string, 'details' | 'terminal'>();
  return (runId: string | null, status: string, stage: string): boolean => {
    if (!runId || loaded.get(runId) === 'terminal') return false;
    if (['completed', 'failed', 'interrupted'].includes(status)) {
      loaded.set(runId, 'terminal');
      return true;
    }
    if (
      status === 'running' &&
      stage.startsWith('detail') &&
      !loaded.has(runId)
    ) {
      loaded.set(runId, 'details');
      return true;
    }
    return false;
  };
}

// A terminal read supersedes an outstanding early read, including its failure.
export function createLatestWorkspaceRead() {
  let generation = 0;
  return async <T>(
    read: () => Promise<T>,
    apply: (value: T) => void,
    fail: () => void,
  ): Promise<void> => {
    const current = ++generation;
    try {
      const value = await read();
      if (current === generation) apply(value);
    } catch {
      if (current === generation) fail();
    }
  };
}
