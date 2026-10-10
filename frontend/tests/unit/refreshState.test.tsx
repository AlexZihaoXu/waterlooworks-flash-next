import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../../src/app/App';
import {
  refreshInterruption,
  refreshRunning,
} from '../../src/lib/refreshState';
import type { WorkspaceState } from '../../src/lib/workspace';
import { refreshSnapshot } from '../support/refreshSnapshot';

const empty: WorkspaceState = {
  status: 'ready',
  postings: [],
  manual_refresh_available: true,
  details_available: false,
  compensation_sort_available: true,
};

describe('refresh run state', () => {
  it('treats a restored stale running row as interrupted, not running', () => {
    const stale = refreshSnapshot({
      run_id: 'r1',
      status: 'running',
      stage: 'recovery_required',
    });
    expect(refreshRunning(stale)).toBe(false);
    expect(refreshInterruption(stale)).toBe('recovery_required');
  });
  it('recognizes finalized interrupted runs and live runs', () => {
    expect(
      refreshInterruption(
        refreshSnapshot({ status: 'interrupted', stage: 'stopped' }),
      ),
    ).toBe('interrupted');
    const live = refreshSnapshot({ status: 'running', stage: 'listings' });
    expect(refreshRunning(live)).toBe(true);
    expect(refreshInterruption(live)).toBeNull();
    expect(refreshRunning(null)).toBe(false);
    expect(refreshInterruption(null)).toBeNull();
    expect(
      refreshInterruption(
        refreshSnapshot({ status: 'failed', stage: 'stopped' }),
      ),
    ).toBeNull();
  });

  it('shows a recovery notice instead of a live refresh after a crash', () => {
    render(
      <App
        state={empty}
        refresh={refreshSnapshot({
          run_id: 'r1',
          status: 'running',
          stage: 'recovery_required',
        })}
      />,
    );
    const alert = screen
      .getByText('Previous refresh was interrupted')
      .closest('.alert') as HTMLElement;
    expect(alert).toHaveClass('alert--warning');
    expect(
      within(alert).getByText(/manual recovery is required/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByText('Refreshing…')).not.toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Refresh postings' }),
    ).toBeDisabled();
  });

  it('explains a finalized interrupted run without blocking a new refresh', () => {
    render(
      <App
        state={empty}
        refresh={refreshSnapshot({
          run_id: 'r1',
          status: 'interrupted',
          stage: 'stopped',
        })}
      />,
    );
    const alert = screen
      .getByText('Previous refresh was interrupted')
      .closest('.alert') as HTMLElement;
    expect(within(alert).getByText(/may be incomplete/i)).toBeInTheDocument();
    expect(
      within(alert).queryByText(/manual recovery is required/i),
    ).not.toBeInTheDocument();
  });
});
