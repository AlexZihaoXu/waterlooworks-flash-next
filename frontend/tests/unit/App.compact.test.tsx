import { readFileSync } from 'node:fs';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from '../../src/app/App';
import { AccountMenu } from '../../src/app/AuthenticatedApp';
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

  it('keeps one header row: logo, short name, theme icon and avatar', async () => {
    vi.stubGlobal('innerWidth', 390);
    render(
      <App
        state={{ ...empty, manual_refresh_available: true }}
        account={<AccountMenu profile={null} signOut={() => {}} />}
      />,
    );
    const banner = screen.getByRole('banner');
    const title = within(banner).getByRole('heading', {
      name: 'WaterlooWorks Flash Next',
      level: 1,
    });
    expect(title).toHaveTextContent(/^Flash$/);
    expect(banner).not.toHaveTextContent(/Updated|Find your next role/);
    expect(
      within(banner).getByRole('button', { name: 'Account menu' }),
    ).toBeInTheDocument();
    // Refresh lives in the search row, not the header.
    expect(
      within(banner).queryByRole('button', { name: 'Refresh postings' }),
    ).not.toBeInTheDocument();
    const refresh = screen.getByRole('button', { name: 'Refresh postings' });
    expect(refresh).toHaveClass('button--lg', 'button--icon-only');
    expect(refresh.closest('.search-row')).not.toBeNull();
    // No big Postings heading row above the search on phones.
    expect(
      screen.queryByRole('heading', { name: 'Postings' }),
    ).not.toBeInTheDocument();
    // The theme icon menu stays in the header when signed in.
    fireEvent.click(
      within(banner).getByRole('button', { name: 'Color theme' }),
    );
    const menu = await screen.findByRole('menu', { name: 'Color theme' });
    fireEvent.click(within(menu).getByRole('menuitemradio', { name: 'Dark' }));
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
  });

  it('hides the refresh action when manual refresh is unavailable', () => {
    vi.stubGlobal('innerWidth', 390);
    render(<App state={empty} />);
    expect(
      screen.queryByRole('button', { name: 'Refresh postings' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Manual refresh is unavailable/)).toBeNull();
  });

  it('puts search, a filter button with its count and a sort button on one row', () => {
    vi.stubGlobal('innerWidth', 390);
    window.history.replaceState(null, '', '/?city=Waterloo&organization=Acme');
    render(<App state={empty} />);
    const search = screen.getByRole('searchbox', {
      name: 'Search titles, companies, locations, and posting IDs',
    });
    const row = search.closest('.search-row') as HTMLElement;
    const filters = within(row).getByRole('button', { name: 'Filters (2)' });
    expect(filters).toHaveClass('button--lg', 'button--icon-only');
    expect(filters).toHaveAttribute('aria-haspopup', 'dialog');
    expect(within(row).getByText('2')).toBeVisible();
    const sort = within(row).getByRole('button', {
      name: 'Sort: Default (posting ID)',
    });
    expect(sort).toHaveClass('button--lg', 'button--icon-only');
    expect(
      screen.queryByRole('complementary', { name: 'Filters' }),
    ).not.toBeInTheDocument();
  });

  it('sorts from an icon menu and keeps the filter drawer for filters', async () => {
    vi.stubGlobal('innerWidth', 390);
    render(<App state={{ ...empty, compensation_sort_available: false }} />);
    expect(
      screen.getByText('0 postings · Page 1/1').closest('.results-caption'),
    ).not.toBeNull();
    expect(
      screen.queryByRole('button', { name: /Sort by/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Sort: Default (posting ID)' }),
    );
    const menu = await screen.findByRole('menu');
    expect(
      within(menu).getByRole('menuitemradio', { name: 'Default (posting ID)' }),
    ).toHaveAttribute('aria-checked', 'true');
    expect(
      within(menu).getByRole('menuitemradio', {
        name: 'Compensation: Average',
      }),
    ).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(
      within(menu).getByRole('menuitemradio', { name: 'Most applications' }),
    );
    expect(
      screen.getByRole('button', { name: 'Sort: Most applications' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
    const drawer = await screen.findByRole('dialog', { name: 'Filters' });
    expect(
      within(drawer).getByRole('button', { name: 'City All cities' }),
    ).toBeInTheDocument();
    expect(
      within(drawer).queryByRole('button', { name: /Sort by/ }),
    ).not.toBeInTheDocument();
  });

  it('lets the page scroll the cards under a compact floating pager', () => {
    vi.stubGlobal('innerWidth', 390);
    render(<App state={empty} />);
    const cards = screen.getByRole('region', { name: 'Posting cards' });
    expect(cards).not.toHaveClass('scroll-shadow');
    const pages = screen.getByRole('navigation', { name: 'Posting pages' });
    expect(pages.closest('.pagination-float')).not.toBeNull();
    expect(pages.closest('.pagination-dock')).toBeNull();
    // Previous / current-of-total / next, with 44px HeroUI buttons.
    for (const name of ['Previous page', 'Next page']) {
      const button = within(pages).getByRole('button', { name });
      expect(button).toHaveClass('button--lg', 'button--icon-only');
      expect(button).toBeDisabled();
    }
    expect(within(pages).getByText('1 of 1')).toBeInTheDocument();
    // The caption stays at the end of the list, not in the floating pill.
    const caption = screen.getByText('0 postings · Page 1/1');
    expect(caption.closest('.results-caption')).not.toBeNull();
    expect(pages).not.toContainElement(caption);
  });

  it('reserves list space for the floating pager above the safe area', () => {
    const css = readFileSync('src/styles/index.css', 'utf8');
    const pager = /\.pagination-float\s*\{([^}]*)\}/.exec(css)![1];
    expect(pager).toMatch(/position:\s*fixed/);
    expect(pager).toMatch(/bottom:[^;]*env\(safe-area-inset-bottom\)/);
    expect(css).toMatch(/--pager-space/);
    expect(css).toMatch(/padding-bottom:\s*var\(--pager-space\)/);
  });

  it('shows only the logo, a theme menu and the sign-in content when signed out', () => {
    vi.stubGlobal('innerWidth', 390);
    render(
      <App
        state={{ status: 'signed-out' }}
        signedOutContent={<p>Sign-in placeholder</p>}
      />,
    );
    const banner = screen.getByRole('banner');
    expect(banner).not.toHaveTextContent(/Find your next role/);
    expect(
      within(banner).getByRole('button', { name: 'Color theme' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Sign-in placeholder')).toBeInTheDocument();
    for (const name of [/Refresh postings/, /^Filters/, /^Sort/])
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    expect(screen.queryByRole('searchbox')).not.toBeInTheDocument();
    expect(screen.queryByText(/Waiting for saved postings/)).toBeNull();
    expect(
      screen.queryByRole('navigation', { name: 'Posting pages' }),
    ).not.toBeInTheDocument();
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
    expect(
      screen.getByRole('heading', { name: 'Postings', level: 2 }),
    ).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Posting cards' })).toHaveClass(
      'scroll-shadow',
    );
    expect(
      screen
        .getByRole('navigation', { name: 'Posting pages' })
        .closest('.pagination-dock'),
    ).not.toBeNull();
  });
});
