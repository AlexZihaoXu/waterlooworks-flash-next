import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RefreshActivity } from '../../src/app/RefreshActivity';
import { refreshSnapshot } from '../support/refreshSnapshot';

afterEach(() => vi.unstubAllGlobals());

// Generic counters only; the server already strips the log for members, and
// the UI never offers log or diagnostic surfaces to them either.
const running = refreshSnapshot({
  run_id: 'run-1',
  status: 'running',
  stage: 'details',
  details_total: 40,
  details_completed: 5,
  detail_requests: 9,
  logs: [
    {
      sequence: 1,
      at: '2026-01-01T12:00:00.000Z',
      level: 'info' as const,
      message: 'Placeholder log line',
    },
  ],
});

describe('member refresh activity', () => {
  it.each([1280, 390])(
    'shows progress without log, counters or details at %ipx',
    (width) => {
      vi.stubGlobal('innerWidth', width);
      render(<RefreshActivity refresh={running} />);
      expect(screen.getByRole('progressbar')).toBeInTheDocument();
      expect(
        screen.queryByText('Placeholder log line'),
      ).not.toBeInTheDocument();
      expect(screen.queryByRole('log')).not.toBeInTheDocument();
      expect(
        screen.queryByRole('button', { name: 'Expand refresh log' }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole('button', { name: 'Refresh details' }),
      ).not.toBeInTheDocument();
      expect(screen.queryByText(/requests/)).not.toBeInTheDocument();
    },
  );

  it('keeps a plain progress line on desktop', () => {
    vi.stubGlobal('innerWidth', 1280);
    render(<RefreshActivity refresh={running} />);
    expect(screen.getByText('5 / 40 descriptions')).toBeInTheDocument();
  });
});
