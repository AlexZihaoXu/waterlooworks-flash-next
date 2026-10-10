import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import {
  STREAM_INTERRUPTED_GRACE_MS,
  useWorkspace,
} from '../../src/lib/useWorkspace';
import { refreshSnapshot } from '../support/refreshSnapshot';

// An empty workspace and the default refresh snapshot are state-machine inputs
// only; no posting data is fabricated.
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener() {}
  close() {
    this.closed = true;
  }
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  FakeEventSource.instances = [];
});

async function connected() {
  vi.stubGlobal('EventSource', FakeEventSource);
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (url) =>
    Response.json(
      String(url) === '/api/workspace'
        ? {
            status: 'ready',
            postings: [],
            manual_refresh_available: true,
            details_available: false,
          }
        : refreshSnapshot(),
    ),
  );
  const hook = renderHook(() => useWorkspace(true));
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  const source = FakeEventSource.instances[0];
  act(() => source.onopen?.());
  expect(hook.result.current.streamConnected).toBe(true);
  return { ...hook, source };
}

it('does not flash the interrupted warning for a quick automatic reconnect', async () => {
  const { result, source } = await connected();
  vi.useFakeTimers();
  // Session rotation ends the old stream; EventSource reconnects by itself.
  act(() => source.onerror?.());
  expect(result.current.streamConnected).toBe(false);
  expect(result.current.streamInterrupted).toBe(false);
  act(() => vi.advanceTimersByTime(STREAM_INTERRUPTED_GRACE_MS - 100));
  act(() => source.onopen?.());
  act(() => vi.advanceTimersByTime(STREAM_INTERRUPTED_GRACE_MS));
  expect(result.current.streamConnected).toBe(true);
  expect(result.current.streamInterrupted).toBe(false);
});

it('still reports a stream that stays down past the grace period', async () => {
  const { result, source, unmount } = await connected();
  vi.useFakeTimers();
  act(() => source.onerror?.());
  act(() => vi.advanceTimersByTime(STREAM_INTERRUPTED_GRACE_MS));
  expect(result.current.streamInterrupted).toBe(true);
  unmount();
  expect(source.closed).toBe(true);
});
