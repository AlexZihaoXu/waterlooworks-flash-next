import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AccountMenu } from '../../src/app/AuthenticatedApp';
import { RefreshContext } from '../../src/lib/refreshContext';
import type { RefreshScheduleView } from '../../src/lib/refreshSchedule';
import { refreshSnapshot } from '../support/refreshSnapshot';

// Schedule metadata only: HH:MM strings, booleans and timestamps. No postings.
function view(
  overrides: Partial<RefreshScheduleView> = {},
): RefreshScheduleView {
  return {
    timezone: 'America/Toronto',
    times: [],
    enabled: false,
    server_enabled: true,
    next_run_at: null,
    min_interval_minutes: 30,
    max_times: 24,
    last: [],
    failure: null,
    ...overrides,
  };
}

type Call = { path: string; method: string; body: unknown };
type Reply = { status?: number; body: unknown };

function fakeServer(routes: Record<string, (body: unknown) => Reply>): Call[] {
  const calls: Call[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path, method, body });
    const handler = routes[`${method} ${path}`];
    if (!handler) return new Response('{}', { status: 404 });
    const reply = handler(body);
    return new Response(JSON.stringify(reply.body), {
      status: reply.status ?? 200,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  return calls;
}

async function openScheduleTab(snapshot = refreshSnapshot()) {
  render(
    <RefreshContext value={snapshot}>
      <AccountMenu
        profile={null}
        signOut={() => {}}
        role="admin"
        uid="admin-a"
      />
    </RefreshContext>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Account menu' }));
  fireEvent.click(await screen.findByRole('tab', { name: 'Schedule' }));
  await screen.findByText('Times are in Toronto time (ET).');
}

function type(element: HTMLElement, data: string) {
  fireEvent(
    element,
    new InputEvent('beforeinput', {
      data,
      inputType: 'insertText',
      bubbles: true,
      cancelable: true,
    }),
  );
}

afterEach(() => vi.restoreAllMocks());

describe('Admin settings Schedule tab', () => {
  it('shows the empty state and a sorted 12-hour list with labelled remove buttons', async () => {
    let current = view();
    const calls = fakeServer({
      'GET /api/admin/refresh-schedule': () => ({ body: current }),
      'DELETE /api/admin/refresh-schedule': (body) => {
        const { time } = body as { time: string };
        current = view({ times: current.times.filter((t) => t !== time) });
        return { body: current };
      },
    });
    await openScheduleTab();
    expect(screen.getByText('No scheduled refreshes.')).toBeVisible();
    current = view({ times: ['07:30', '13:00', '19:00'] });
    fireEvent.click(screen.getByRole('tab', { name: 'Refresh' }));
    fireEvent.click(screen.getByRole('tab', { name: 'Schedule' }));
    const list = await screen.findByRole('list');
    expect(
      within(list)
        .getAllByRole('listitem')
        .map((row) => row.textContent),
    ).toEqual(['7:30 AM', '1:00 PM', '7:00 PM']);
    expect(screen.getByLabelText('Scheduled times')).toContainElement(list);
    fireEvent.click(screen.getByRole('button', { name: 'Remove 1:00 PM' }));
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Remove 1:00 PM' }),
      ).not.toBeInTheDocument(),
    );
    expect(calls.find((call) => call.method === 'DELETE')?.body).toEqual({
      time: '13:00',
    });
  });

  it('adds a time from the HeroUI TimeField and shows the backend validation error inline', async () => {
    let current = view({ times: ['07:30'] });
    const calls = fakeServer({
      'GET /api/admin/refresh-schedule': () => ({ body: current }),
      'POST /api/admin/refresh-schedule': (body) => {
        const { time } = body as { time: string };
        if (time === '07:45')
          return {
            status: 422,
            body: {
              detail: 'Scheduled times must be at least 30 minutes apart.',
            },
          };
        current = view({ times: [...current.times, time].sort() });
        return { body: current };
      },
    });
    await openScheduleTab();
    const add = screen.getByRole('button', { name: 'Add' });
    expect(add).toBeDisabled();
    const [hour, minute] = within(
      screen.getByRole('form', { name: 'Add a scheduled time' }),
    ).getAllByRole('spinbutton');
    hour.focus();
    type(hour, '7');
    type(minute, '4');
    type(minute, '5');
    fireEvent.click(add);
    expect(
      await screen.findByText(
        'Scheduled times must be at least 30 minutes apart.',
      ),
    ).toBeVisible();
    expect(calls.at(-1)).toMatchObject({
      method: 'POST',
      body: { time: '07:45' },
    });
    hour.focus();
    type(hour, '8');
    type(minute, '1');
    type(minute, '5');
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(
      await screen.findByRole('button', { name: 'Remove 8:15 AM' }),
    ).toBeVisible();
    expect(
      screen.queryByText('Scheduled times must be at least 30 minutes apart.'),
    ).not.toBeInTheDocument();
    expect(
      within(screen.getByRole('list'))
        .getAllByRole('listitem')
        .map((row) => row.textContent),
    ).toEqual(['7:30 AM', '8:15 AM']);
  });

  it('toggles scheduling and explains when the server gate is off', async () => {
    let current = view({ times: ['07:30'], server_enabled: false });
    const calls = fakeServer({
      'GET /api/admin/refresh-schedule': () => ({ body: current }),
      'PUT /api/admin/refresh-schedule/enabled': (body) => {
        current = view({ ...current, ...(body as object) });
        return { body: current };
      },
    });
    await openScheduleTab();
    expect(
      screen.getByText('Scheduling is turned off on this server'),
    ).toBeVisible();
    const toggle = screen.getByRole('switch', {
      name: 'Run refreshes automatically at these times',
    });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle).toBeChecked());
    expect(calls.at(-1)).toMatchObject({
      path: '/api/admin/refresh-schedule/enabled',
      method: 'PUT',
      body: { enabled: true },
    });
    expect(
      screen.getByText(/The switch is on, but nothing runs/),
    ).toBeVisible();
  });

  it('shows the failure banner and the failed run log when it is the latest run', async () => {
    fakeServer({
      'GET /api/admin/refresh-schedule': () => ({
        body: view({
          times: ['07:30'],
          failure: {
            run_id: 'run-failed',
            slot: '2026-10-10 07:30',
            failed_at: '2026-10-10T11:30:00+00:00',
            category: 'sign_in',
          },
        }),
      }),
    });
    await openScheduleTab(
      refreshSnapshot({
        run_id: 'run-failed',
        status: 'failed',
        logs: [
          {
            sequence: 1,
            at: '2026-10-10T11:30:01+00:00',
            level: 'info',
            message: 'Fresh authentication starting.',
          },
        ],
      }),
    );
    expect(
      screen.getByText(
        'Scheduled refresh at Oct 10, 2026, 7:30 AM EDT failed. Scheduling was turned off.',
      ),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'View log' }));
    expect(
      await screen.findByRole('button', { name: 'Expand refresh log' }),
    ).toBeVisible();
  });
});
