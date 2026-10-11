import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../../src/app/App';
import { RefreshTimes } from '../../src/app/RefreshTimes';
import type { WorkspaceState } from '../../src/lib/workspace';
import { refreshSnapshot } from '../support/refreshSnapshot';

// Timestamps only; the workspace is empty. No posting data.
const NOW = Date.parse('2026-10-10T16:00:00Z');
const at = (offset: number) => new Date(NOW + offset).toISOString();

describe('relative refresh times', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it('ticks every second while the next run is under a minute away', () => {
    render(
      <RefreshTimes
        lastRefreshed={at(-3 * 3_600_000)}
        nextScheduled={at(3_000)}
      />,
    );
    expect(
      screen.getByText('Refreshed 3 hours ago · Next in 3 seconds'),
    ).toBeVisible();
    act(() => vi.advanceTimersByTime(1_000));
    expect(
      screen.getByText('Refreshed 3 hours ago · Next in 2 seconds'),
    ).toBeVisible();
    // Each tick schedules the next one after React commits.
    for (let step = 0; step < 3; step += 1)
      act(() => vi.advanceTimersByTime(1_000));
    // A past next run is omitted until the server publishes the next one.
    expect(screen.getByText('Refreshed 3 hours ago')).toBeVisible();
  });

  it('ticks about every 30 seconds otherwise and on returning to the tab', () => {
    render(
      <RefreshTimes
        lastRefreshed={at(-3 * 60_000 - 10_000)}
        nextScheduled={at(5 * 3_600_000 + 30 * 60_000)}
      />,
    );
    const text = (minutes: number) =>
      `Refreshed ${minutes} minutes ago · Next in 5 hours`;
    expect(screen.getByText(text(3))).toBeVisible();
    act(() => vi.advanceTimersByTime(29_000));
    expect(screen.getByText(text(3))).toBeVisible();
    act(() => vi.advanceTimersByTime(1_000));
    expect(screen.getByText(text(3))).toBeVisible();
    act(() => vi.advanceTimersByTime(30_000));
    expect(screen.getByText(text(4))).toBeVisible();
    // Hidden tabs throttle timers; visibility return refreshes immediately.
    vi.setSystemTime(NOW + 20 * 60_000);
    act(() => {
      fireEvent(document, new Event('visibilitychange'));
    });
    expect(screen.getByText(/Refreshed 23 minutes ago/)).toBeVisible();
  });

  it('keeps the exact Toronto times in a keyboard-focusable tooltip trigger', () => {
    render(
      <RefreshTimes
        lastRefreshed="2026-10-10T13:00:00Z"
        nextScheduled="2026-10-10T23:00:00Z"
      />,
    );
    const trigger = screen.getByRole('button', {
      name: /Last refreshed Oct 10, 2026, 9:00 AM EDT\. Next scheduled refresh Oct 10, 2026, 7:00 PM EDT/,
    });
    expect(trigger).toHaveAttribute('tabindex', '0');
  });

  it('says never refreshed and omits the next run when none is scheduled', () => {
    render(<RefreshTimes lastRefreshed={undefined} nextScheduled={null} />);
    expect(screen.getByText('Never refreshed')).toBeVisible();
  });

  it('uses the narrow form on phones', () => {
    render(
      <RefreshTimes
        narrow
        lastRefreshed={at(-3 * 3_600_000)}
        nextScheduled={at(5 * 3_600_000)}
      />,
    );
    expect(screen.getByText('Refreshed 3h ago · next in 5h')).toBeVisible();
  });
});

const empty: WorkspaceState = {
  status: 'ready',
  postings: [],
  manual_refresh_available: false,
  details_available: false,
  compensation_sort_available: true,
};
const failed = refreshSnapshot({
  scheduled_failure: { failed: true, failed_at: '2026-10-10T11:30:00+00:00' },
});

describe('scheduled refresh failure notice', () => {
  it('asks members to contact an admin without any category or run detail', () => {
    render(<App state={empty} refresh={failed} />);
    expect(
      screen.getByText(
        'Scheduled refresh at Oct 10, 2026, 7:30 AM EDT failed. Please ask an admin to check the log.',
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole('button', { name: 'Open Schedule settings' }),
    ).not.toBeInTheDocument();
  });

  it('links admins to Settings › Schedule', () => {
    const open = vi.fn();
    render(<App state={empty} refresh={failed} onOpenAdminSettings={open} />);
    expect(
      screen.getByText(
        'Scheduled refresh at Oct 10, 2026, 7:30 AM EDT failed. Scheduling was turned off.',
      ),
    ).toBeVisible();
    fireEvent.click(
      screen.getByRole('button', { name: 'Open Schedule settings' }),
    );
    expect(open).toHaveBeenCalledWith('schedule');
  });

  it('shows the header relative times with the next scheduled run', () => {
    render(
      <App
        state={empty}
        refresh={refreshSnapshot({
          next_scheduled_at: new Date(
            Date.now() + 5 * 3_600_000 + 60_000,
          ).toISOString(),
        })}
      />,
    );
    expect(
      screen.getAllByText('Never refreshed · Next in 5 hours')[0],
    ).toBeVisible();
  });
});
