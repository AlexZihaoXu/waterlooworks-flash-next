import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../../src/app/App';

describe('App', () => {
  it('shows a placeholder without fabricated job postings', () => {
    render(<App />);
    expect(
      screen.getByRole('heading', { name: 'WaterlooWorks Flash Next' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Placeholder — guided exploration has not started.'),
    ).toBeInTheDocument();
  });
});
