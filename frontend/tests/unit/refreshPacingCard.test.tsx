import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { RefreshPacingCard } from '../../src/app/AuthenticatedApp';

// The backend default policy (api_models defaults); counters only, no source data.
const pacing = {
  request_cap: 400,
  daily_request_cap: 1200,
  min_interval_minutes: 30,
  min_gap_seconds: 3,
  max_gap_seconds: 6,
  rest_every_min: 20,
  rest_every_max: 30,
  rest_min_seconds: 45,
  rest_max_seconds: 120,
  used_24h: 0,
  remaining_24h: 1200,
  next_allowed_at: null,
  blocked_until: null,
  last_pacing_stop: null,
  last_error_category: null,
};

it('shows the policy, rolling usage and that a refresh is allowed', () => {
  render(<RefreshPacingCard pacing={pacing} />);
  expect(screen.getByText(/waits 3–6 s/)).toBeInTheDocument();
  expect(
    screen.getByRole('meter', {
      name: 'WaterlooWorks requests in the last 24 hours',
    }),
  ).toBeInTheDocument();
  expect(screen.getByText(/1200 requests remaining/)).toBeInTheDocument();
  expect(screen.getByText('Refresh allowed now.')).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('flags a ban-risk stop and the active block prominently', () => {
  const until = new Date(Date.now() + 6 * 3600 * 1000).toISOString();
  render(
    <RefreshPacingCard
      pacing={{
        ...pacing,
        used_24h: 37,
        remaining_24h: 1163,
        blocked_until: until,
        next_allowed_at: until,
        last_pacing_stop: 'ban_risk',
        last_error_category: 'rate_limit',
      }}
    />,
  );
  expect(
    screen.getByText('Ban-risk warning from WaterlooWorks'),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/stopped at once \(rate_limit\)/),
  ).toBeInTheDocument();
  expect(screen.getByText(/Refresh blocked until/)).toBeInTheDocument();
  expect(screen.getByText(/Next refresh allowed at/)).toBeInTheDocument();
});

it('explains a partial finish at a request cap', () => {
  render(
    <RefreshPacingCard
      pacing={{ ...pacing, last_pacing_stop: 'request_cap' }}
    />,
  );
  expect(
    screen.getByText(
      'Finished partially: per-run WaterlooWorks request cap reached',
    ),
  ).toBeInTheDocument();
});

it('shows a loading description before the first status read', () => {
  render(<RefreshPacingCard pacing={null} />);
  expect(screen.getByText('Pacing status is loading.')).toBeInTheDocument();
});
