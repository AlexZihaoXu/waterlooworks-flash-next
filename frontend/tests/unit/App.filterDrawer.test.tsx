import { readFileSync } from 'node:fs';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { App } from '../../src/app/App';
import type { WorkspaceState } from '../../src/lib/workspace';

afterEach(() => vi.unstubAllGlobals());

// An empty ready database; selections come only from the shared URL.
const empty: WorkspaceState = {
  status: 'ready',
  postings: [],
  manual_refresh_available: false,
  details_available: false,
  compensation_sort_available: true,
};

it('moves filters into a HeroUI Drawer on phone widths with full trigger text', async () => {
  vi.stubGlobal('innerWidth', 320);
  window.history.replaceState(null, '', '/?city=Waterloo&organization=Acme');
  render(<App state={empty} />);
  // Only one compact trigger beside search; the filter grid is not squeezed in.
  expect(
    screen.queryByRole('button', { name: /^City/ }),
  ).not.toBeInTheDocument();
  const open = screen.getByRole('button', { name: 'Filters (2)' });
  expect(open).toHaveAttribute('aria-haspopup', 'dialog');
  fireEvent.click(open);
  const drawer = await screen.findByRole('dialog', { name: 'Filters' });
  // Full visible values, not a bare count or "All".
  expect(
    within(drawer).getByRole('button', { name: 'City Waterloo' }),
  ).toBeInTheDocument();
  expect(
    within(drawer).getByRole('button', { name: 'Organization Acme' }),
  ).toBeInTheDocument();
  expect(
    within(drawer).getByRole('button', {
      name: 'Country All countries',
    }),
  ).toBeInTheDocument();
  expect(
    within(drawer).getByRole('button', { name: /Compensation format/ }),
  ).toBeInTheDocument();
});

it('keeps the inline filter sidebar on wider screens', () => {
  vi.stubGlobal('innerWidth', 1024);
  render(<App state={empty} />);
  const sidebar = screen.getByRole('complementary', { name: 'Filters' });
  expect(
    within(sidebar).getByRole('button', { name: 'City All cities' }),
  ).toBeInTheDocument();
  expect(
    within(sidebar).queryByRole('button', { name: /^Filters/ }),
  ).not.toBeInTheDocument();
});

it('no longer forces a 64rem phone layout or a lone fourth filter column', () => {
  const css = readFileSync('src/styles/index.css', 'utf8');
  expect(css).not.toMatch(/min-height:\s*64rem/);
  expect(css).not.toMatch(/repeat\(3,\s*minmax\(0,\s*1fr\)\)/);
});
