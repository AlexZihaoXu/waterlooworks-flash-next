import { useEffect, useState } from 'react';

export function useRefreshEta(
  runId: string | null,
  running: boolean,
  estimate: number | null,
  sample: number = 0,
) {
  const available = running && estimate !== null;
  const [state, setState] = useState(() => ({
    runId,
    running,
    estimate,
    sample,
    remaining: available ? Math.max(0, estimate) : null,
  }));
  if (
    state.runId !== runId ||
    state.running !== running ||
    state.estimate !== estimate ||
    state.sample !== sample
  ) {
    // Each completed-page sample supersedes the old approximation, even when
    // its rounded estimate is identical or higher. Ordinary renders do not.
    const remaining = available ? Math.max(0, estimate) : null;
    setState({ runId, running, estimate, sample, remaining });
  }
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(
      () =>
        setState((previous) =>
          previous.remaining === null || previous.remaining <= 0
            ? previous
            : { ...previous, remaining: previous.remaining - 1 },
        ),
      1200,
    );
    return () => window.clearInterval(timer);
  }, [runId, running, estimate, sample]);
  return state.remaining;
}

export function useRefreshLogVisibility(
  runId: string | null,
  running: boolean,
) {
  const [state, setState] = useState({ runId, running, visible: running });
  if (state.runId !== runId || state.running !== running) {
    setState({
      runId,
      running,
      visible: running || (state.runId === runId && state.visible),
    });
  }
  useEffect(() => {
    if (running || !state.visible) return;
    const timer = window.setTimeout(
      () => setState((previous) => ({ ...previous, visible: false })),
      3000,
    );
    return () => window.clearTimeout(timer);
  }, [runId, running, state.visible]);
  return state.visible;
}
