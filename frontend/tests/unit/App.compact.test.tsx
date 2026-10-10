import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from '../../src/app/App';
import type { WorkspaceState } from '../../src/lib/workspace';

afterEach(() => vi.unstubAllGlobals());

// An empty ready database; no posting fixtures.
const empty: WorkspaceState = {
  status: 'ready',
  postings: [],
  manual_refresh_available: false,
  details_available: false,
  compensation_sort_available: true,
};

describe('phone workspace chrome', () => {
  it('replaces the three-button theme group with one compact theme menu', async () => {
    vi.stubGlobal('innerWidth', 390);
    render(<App state={empty} />);
    expect(
      screen.getByRole('heading', {
        name: 'WaterlooWorks Flash Next',
        level: 1,
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('radiogroup', { name: 'Color theme' }),
    ).not.toBeInTheDocument();
    const trigger = screen.getByRole('button', { name: 'Color theme' });
    expect(trigger).toHaveClass('button--lg', 'button--icon-only');
    fireEvent.click(trigger);
    const menu = await screen.findByRole('menu', { name: 'Color theme' });
    expect(
      within(menu).getByRole('menuitemradio', { name: 'System' }),
    ).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(within(menu).getByRole('menuitemradio', { name: 'Dark' }));
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(localStorage.getItem('heroui-theme')).toBe('dark');
  });

  it('puts search and a 44px filter button with its count on one row', () => {
    vi.stubGlobal('innerWidth', 390);
    window.history.replaceState(null, '', '/?city=Waterloo&organization=Acme');
    render(<App state={empty} />);
    const search = screen.getByRole('searchbox', {
      name: 'Search titles, companies, locations, and posting IDs',
    });
    const filters = screen.getByRole('button', { name: 'Filters (2)' });
    expect(filters).toHaveClass('button--lg', 'button--icon-only');
    expect(filters).toHaveAttribute('aria-haspopup', 'dialog');
    expect(filters.closest('.search-row')).toContainElement(search);
    expect(
      within(filters.closest('.search-row')!).getByText('2'),
    ).toBeVisible();
    expect(
      screen.queryByRole('complementary', { name: 'Filters' }),
    ).not.toBeInTheDocument();
  });

  it('moves sort into the filter drawer and shortens the count line', async () => {
    vi.stubGlobal('innerWidth', 390);
    render(<App state={empty} />);
    expect(screen.getByText('0 postings · Page 1/1')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Sort by/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
    const drawer = await screen.findByRole('dialog', { name: 'Filters' });
    expect(
      within(drawer).getByRole('button', { name: /Sort by/ }),
    ).toBeInTheDocument();
    expect(
      within(drawer).getByRole('button', { name: 'City All cities' }),
    ).toBeInTheDocument();
  });

  it('keeps the inline theme group, sidebar and toolbar sort on wider screens', () => {
    vi.stubGlobal('innerWidth', 1024);
    render(<App state={empty} />);
    expect(
      screen.getByRole('radiogroup', { name: 'Color theme' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('complementary', { name: 'Filters' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /Sort by$/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByText('0 postings · Page 1/1 · Showing 0 on this page'),
    ).toBeInTheDocument();
  });
});
