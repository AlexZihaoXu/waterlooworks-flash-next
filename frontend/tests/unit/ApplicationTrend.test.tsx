import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { ApplicationTrend } from '../../src/app/ApplicationTrend';

it('shows an honest empty state without seeding a chart', () => {
  render(<ApplicationTrend observations={[]} />);
  expect(
    screen.getByRole('heading', { name: 'Application trend' }),
  ).toBeInTheDocument();
  expect(
    screen.getByText('No saved numeric application counts yet.'),
  ).toBeInTheDocument();
  expect(screen.queryByRole('group')).not.toBeInTheDocument();
});
