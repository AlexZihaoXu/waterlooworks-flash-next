import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { App } from '../../src/app/App';

// An empty ready database; the pay sort comes only from the shared URL.
it('collapses the pay-sorting method into a HeroUI Disclosure', async () => {
  window.history.replaceState(null, '', '/?sort=compensation-average');
  render(
    <App
      state={{
        status: 'ready',
        postings: [],
        manual_refresh_available: false,
        details_available: false,
        compensation_sort_available: true,
      }}
    />,
  );
  const trigger = screen.getByRole('button', { name: 'How pay sorting works' });
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
  const collapsed = screen.queryByText(/Highest first within comparable/);
  if (collapsed) expect(collapsed).not.toBeVisible();
  fireEvent.click(trigger);
  expect(trigger).toHaveAttribute('aria-expanded', 'true');
  expect(
    await screen.findByText(/Highest first within comparable/),
  ).toBeVisible();
});
