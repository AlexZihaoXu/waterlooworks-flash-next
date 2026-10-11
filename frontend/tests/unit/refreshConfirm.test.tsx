import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { toast } from '@heroui/react';
import { App } from '../../src/app/App';
import { refreshSnapshot } from '../support/refreshSnapshot';

// Live workspace over stubbed HTTP primitives: an empty ready database, the
// default refresh snapshot and the backend default pacing policy. Timestamps
// are relative to now; no posting data or platform response is fabricated.
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener() {}
  close() {}
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  FakeEventSource.instances = [];
  // The HeroUI toast queue is module-global; start each test empty.
  toast.clear();
});

const MINUTE = 60_000;

function pacing(finishedMinutesAgo: number | null, intervalMinutes = 30) {
  const finished =
    finishedMinutesAgo === null
      ? null
      : new Date(Date.now() - finishedMinutesAgo * MINUTE);
  const until =
    finished === null
      ? null
      : new Date(finished.getTime() + intervalMinutes * MINUTE);
  return {
    request_cap: 400,
    daily_request_cap: 1200,
    min_interval_minutes: intervalMinutes,
    min_gap_seconds: 3,
    max_gap_seconds: 6,
    rest_every_min: 20,
    rest_every_max: 30,
    rest_min_seconds: 45,
    rest_max_seconds: 120,
    used_24h: 0,
    remaining_24h: 1200,
    next_allowed_at: null,
    last_finished_at: finished?.toISOString() ?? null,
    cooldown_until:
      until !== null && until.getTime() > Date.now()
        ? until.toISOString()
        : null,
    blocked_until: null,
    last_pacing_stop: null,
    last_error_category: null,
  };
}

type PostReply = () => Response;

async function liveApp({
  finishedMinutesAgo = null as number | null,
  retryAfter = 0,
  posts = [] as PostReply[],
} = {}) {
  vi.stubGlobal('EventSource', FakeEventSource);
  const bodies: (string | null)[] = [];
  const queue = [...posts];
  const fetch = vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(async (input, init) => {
      const url = String(input);
      if (init?.method === 'POST' && url === '/api/refresh') {
        bodies.push(typeof init.body === 'string' ? init.body : null);
        return (queue.shift() ?? (() => new Response(null, { status: 202 })))();
      }
      if (url === '/api/workspace')
        return Response.json({
          status: 'ready',
          postings: [],
          manual_refresh_available: true,
          details_available: false,
        });
      if (url === '/api/refresh')
        return Response.json(refreshSnapshot({ retry_after: retryAfter }));
      if (url === '/api/admin/refresh-auth')
        return Response.json({
          run_id: null,
          approval_required: false,
          code: null,
          expires_in_seconds: 0,
          pacing: pacing(finishedMinutesAgo),
        });
      return new Response(null, { status: 404 });
    });
  render(<App />);
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
  act(() => FakeEventSource.instances[0].onopen?.());
  const button = await screen.findByRole('button', {
    name: 'Refresh postings',
  });
  await waitFor(() => expect(button).toBeEnabled());
  return { button, bodies, fetch };
}

const recent = (finished: string) => () =>
  Response.json(
    { detail: 'The last refresh finished recently.' },
    { status: 409, headers: { 'X-WW-Refresh-Recent': finished } },
  );

describe.each([
  ['desktop', 1280],
  ['phone', 390],
])('refresh confirmation on %s', (_, width) => {
  it('asks before refreshing within the interval; Cancel sends nothing', async () => {
    vi.stubGlobal('innerWidth', width);
    const { button, bodies } = await liveApp({ finishedMinutesAgo: 12 });
    fireEvent.click(button);
    const dialog = await screen.findByRole('alertdialog', {
      name: 'Refresh again?',
    });
    expect(
      within(dialog).getByRole('heading', { name: 'Refresh again?' }),
    ).toBeInTheDocument();
    expect(dialog).toHaveTextContent(
      'The last refresh finished 12 minutes ago. Refreshing again this soon sends more requests to WaterlooWorks.',
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() =>
      expect(
        screen.queryByRole('alertdialog', { name: 'Refresh again?' }),
      ).not.toBeInTheDocument(),
    );
    expect(bodies).toEqual([]);
  });

  it('sends confirm_recent only after Refresh anyway', async () => {
    vi.stubGlobal('innerWidth', width);
    const { button, bodies } = await liveApp({ finishedMinutesAgo: 12 });
    fireEvent.click(button);
    const dialog = await screen.findByRole('alertdialog', {
      name: 'Refresh again?',
    });
    expect(bodies).toEqual([]);
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Refresh anyway' }),
    );
    await waitFor(() => expect(bodies).toEqual(['{"confirm_recent":true}']));
    await waitFor(() =>
      expect(
        screen.queryByRole('alertdialog', { name: 'Refresh again?' }),
      ).not.toBeInTheDocument(),
    );
  });
});

describe('refresh confirmation decisions', () => {
  it('refreshes at once outside the interval', async () => {
    const { button, bodies } = await liveApp({ finishedMinutesAgo: 45 });
    fireEvent.click(button);
    await waitFor(() => expect(bodies).toEqual(['{}']));
    expect(
      screen.queryByRole('alertdialog', { name: 'Refresh again?' }),
    ).not.toBeInTheDocument();
  });

  it('opens the same dialog on the server 409-recent signal', async () => {
    const finished = new Date(Date.now() - 5 * MINUTE).toISOString();
    const { button, bodies } = await liveApp({
      finishedMinutesAgo: null,
      posts: [recent(finished)],
    });
    fireEvent.click(button);
    const dialog = await screen.findByRole('alertdialog', {
      name: 'Refresh again?',
    });
    expect(dialog).toHaveTextContent(
      'The last refresh finished 5 minutes ago.',
    );
    expect(bodies).toEqual(['{}']);
    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Refresh anyway' }),
    );
    await waitFor(() =>
      expect(bodies).toEqual(['{}', '{"confirm_recent":true}']),
    );
  });

  it('keeps a plain 409 as the running notice, without the dialog', async () => {
    const { button, bodies } = await liveApp({
      posts: [
        () =>
          Response.json(
            { detail: 'Refresh already running.' },
            { status: 409 },
          ),
      ],
    });
    fireEvent.click(button);
    expect(await screen.findByText('Refresh already running')).toBeVisible();
    expect(bodies).toEqual(['{}']);
    expect(
      screen.queryByRole('alertdialog', { name: 'Refresh again?' }),
    ).not.toBeInTheDocument();
  });

  it('explains a hard wait without a dialog or a request', async () => {
    const { button, bodies } = await liveApp({
      finishedMinutesAgo: 10,
      retryAfter: 3_600,
    });
    fireEvent.click(button);
    expect(await screen.findByText('Refresh paused')).toBeVisible();
    expect(bodies).toEqual([]);
    expect(
      screen.queryByRole('alertdialog', { name: 'Refresh again?' }),
    ).not.toBeInTheDocument();
  });

  it('shows the hard refusal notice for a server 429, not the dialog', async () => {
    const { button, bodies } = await liveApp({
      posts: [
        () =>
          Response.json(
            { detail: 'Paused.' },
            { status: 429, headers: { 'Retry-After': '3600' } },
          ),
      ],
    });
    fireEvent.click(button);
    expect(await screen.findByText('Refresh paused')).toBeVisible();
    expect(bodies).toEqual(['{}']);
    expect(
      screen.queryByRole('alertdialog', { name: 'Refresh again?' }),
    ).not.toBeInTheDocument();
  });
});
