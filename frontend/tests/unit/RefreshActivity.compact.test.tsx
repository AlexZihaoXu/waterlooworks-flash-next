import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RefreshActivity } from '../../src/app/RefreshActivity';
import { refreshSnapshot } from '../support/refreshSnapshot';

afterEach(() => vi.unstubAllGlobals());

// Contract-default snapshot with generic counters and placeholder log text;
// no posting data, provider output or API responses.
const provider = 'Provider label / with-a-long-model-identifier';
const logs = Array.from({ length: 25 }, (_, index) => ({
  sequence: index + 1,
  at: new Date(Date.UTC(2026, 0, 1, 12, 0, index)).toISOString(),
  level: 'info' as const,
  message: `Log line ${index + 1} ${'long-unbroken-text-'.repeat(6)}`,
}));
const running = refreshSnapshot({
  run_id: 'run-1',
  status: 'running',
  stage: 'analysis',
  analysis_provider: provider,
  analysis_total: 664,
  analysis_completed: 650,
  analysis_accepted: 2,
  analysis_rejected: 8,
  analysis_skipped: 640,
  logs,
});

describe('phone refresh status', () => {
  it('collapses to one short status row with a progress bar', () => {
    vi.stubGlobal('innerWidth', 390);
    render(<RefreshActivity refresh={running} admin />);
    const bar = screen.getByRole('progressbar', {
      name: /^AI extraction progress/,
    });
    expect(within(bar).getByText('Extracting · 650/664')).toBeInTheDocument();
    expect(within(bar).getByText('98%')).toBeInTheDocument();
    // The long provider label, stats line and log stay out of the main view.
    expect(screen.queryByText(new RegExp(provider))).not.toBeInTheDocument();
    expect(screen.queryByText(/processed/)).not.toBeInTheDocument();
    expect(screen.queryByRole('log')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Expand refresh log' }),
    ).not.toBeInTheDocument();
    const open = screen.getByRole('button', { name: 'Refresh details' });
    expect(open).toHaveAttribute('aria-haspopup', 'dialog');
    expect(open).toHaveClass('button--lg');
  });

  it('expands to full stage text, labeled stats and a wrapped newest-first log', async () => {
    vi.stubGlobal('innerWidth', 390);
    render(<RefreshActivity refresh={running} admin />);
    fireEvent.click(screen.getByRole('button', { name: 'Refresh details' }));
    const sheet = await screen.findByRole('dialog', {
      name: 'Refresh progress',
    });
    expect(
      within(sheet).getByText(`Extracting with ${provider}`),
    ).toBeInTheDocument();
    expect(within(sheet).getByText('ETA unavailable')).toBeInTheDocument();
    const stats = within(sheet).getByRole('list', { name: 'Refresh counters' });
    const accepted = within(stats).getByText('Accepted').closest('div')!;
    expect(accepted).toHaveTextContent('Accepted2');
    expect(within(stats).getByText('650 / 664')).toBeInTheDocument();

    const log = within(sheet).getByRole('log', { name: 'Refresh log' });
    const items = within(log).getAllByRole('listitem');
    expect(items).toHaveLength(20);
    expect(items[0]).toHaveTextContent('Log line 25');
    expect(items[19]).toHaveTextContent('Log line 6');
    const message = within(items[0]).getByText(/^Log line 25/);
    expect(message.closest('.refresh-log-message')).not.toBeNull();
    fireEvent.click(
      within(sheet).getByRole('button', { name: 'Show 5 older entries' }),
    );
    expect(within(log).getAllByRole('listitem')).toHaveLength(25);
    expect(
      within(sheet).queryByRole('button', { name: /older entries/ }),
    ).not.toBeInTheDocument();
  });

  it('keeps the inline progress and log console on wider screens', () => {
    vi.stubGlobal('innerWidth', 1024);
    render(<RefreshActivity refresh={running} admin />);
    expect(
      screen.getByText(`Extracting with ${provider} · ETA unavailable`),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Expand refresh log' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Refresh details' }),
    ).not.toBeInTheDocument();
  });
});
