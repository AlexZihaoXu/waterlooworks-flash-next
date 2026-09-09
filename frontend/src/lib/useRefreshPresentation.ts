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

// Local observation only: no listing time, server ETA, or persisted baseline.
export function useDetailEta(
  runId: string | null,
  running: boolean,
  stage: string,
  completed: number,
  total: number,
) {
  const active = running && stage.startsWith('detail');
  const [now, setNow] = useState(() => performance.now());
  const [baseline, setBaseline] = useState({
    runId,
    active,
    total,
    completed,
    started: now,
    latest: completed,
  });
  const reset =
    baseline.runId !== runId ||
    baseline.active !== active ||
    baseline.total !== total ||
    completed < baseline.latest;
  if (reset) {
    setBaseline({
      runId,
      active,
      total,
      completed,
      started: now,
      latest: completed,
    });
  } else if (baseline.latest !== completed) {
    setBaseline({ ...baseline, latest: completed });
  }
  useEffect(() => {
    // Keep the clock current outside details too, so phase entry never includes
    // listing elapsed time. Sampling precision is one second.
    const timer = window.setInterval(() => setNow(performance.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const elapsed = (now - baseline.started) / 1000;
  const observed = completed - baseline.completed;
  if (reset || !active || total <= completed || observed < 2 || elapsed < 2) {
    return null;
  }
  // Recompute throughput even without completions: waits/retries/stalls increase
  // the estimate instead of counting down toward a misleading finish time.
  return Math.ceil(((total - completed) * elapsed) / observed);
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
