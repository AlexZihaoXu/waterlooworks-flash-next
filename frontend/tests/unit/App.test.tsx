import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from '../../src/app/App';

// Pure disconnected/empty presentation states, not fabricated posting/API fixtures.
describe('App', () => {
  it('uses danger status for the disconnected backend notice', () => {
    render(<App state={{ status: 'disconnected' }} />);
    expect(
      screen.getByText('Backend not connected').closest('.alert'),
    ).toHaveClass('alert--danger');
  });
  it('separates filters from the posting results with semantic headings', () => {
    render(<App state={{ status: 'disconnected' }} />);
    expect(
      screen.getByRole('complementary', { name: 'Filters' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Postings', level: 2 }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', {
        name: 'WaterlooWorks Flash Next',
        level: 1,
      }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Refresh log/)).not.toBeInTheDocument();
  });
  it('places refresh beside Postings and names search without a visible label', () => {
    render(<App state={{ status: 'disconnected' }} />);
    const heading = screen.getByRole('heading', { name: 'Postings', level: 2 });
    // One header row: title + live count, then sort + refresh.
    const row = heading.closest('.postings-heading');
    expect(row).toContainElement(
      screen.getByRole('button', { name: 'Refresh postings' }),
    );
    expect(row).toContainElement(
      screen.getByText('Waiting for saved postings'),
    );
    expect(row).toHaveTextContent('Sort by');
    expect(
      screen.getByRole('searchbox', {
        name: 'Search titles, companies, locations, and posting IDs',
      }),
    ).toHaveAttribute('placeholder', 'Search title, company, location, or ID…');
    expect(
      screen.queryByText(
        'Search titles, companies, locations, and posting IDs',
      ),
    ).not.toBeInTheDocument();
  });
  it('keeps the inactive refresh action neutral', () => {
    render(<App state={{ status: 'disconnected' }} />);
    const button = screen.getByRole('button', { name: 'Refresh postings' });
    expect(button).toHaveClass('button--tertiary');
    expect(within(button).getByText('Refresh postings')).toHaveClass(
      'typography--color-muted',
    );
  });
  it('hides the postings scrollbar without removing its scroll region', () => {
    render(<App state={{ status: 'disconnected' }} />);
    expect(screen.getByRole('region', { name: 'Posting cards' })).toHaveClass(
      'scroll-shadow--hide-scrollbar',
    );
  });
  it('orders the filter card fields beneath its heading, clear action, and divider', () => {
    render(<App state={{ status: 'disconnected' }} />);
    const card = within(
      screen
        .getByRole('heading', { name: 'Filters' })
        .closest('[data-slot="card"]') as HTMLElement,
    );
    expect(card.getByRole('heading', { name: 'Filters' })).toBeInTheDocument();
    expect(card.getByRole('separator')).toBeInTheDocument();
    expect(card.getByRole('button', { name: 'Clear filters' })).toBeDisabled();
    const names = [
      'Organization All organizations',
      'Country All countries',
      'City All cities',
      'Targeted disciplines All targeted disciplines',
    ];
    for (const name of names) {
      const button = card.getByRole('button', { name });
      expect(button).toHaveClass('button--tertiary');
      expect(button.querySelector('.typography--color-muted')).not.toBeNull();
      // WCAG 2.5.3: the visible label and value form the name; no aria-label
      // replaces the text a voice-control user can see.
      expect(button).not.toHaveAttribute('aria-label');
      expect(name).toContain(button.textContent!.trim());
    }
    expect(
      card
        .getAllByRole('button')
        .map((button) => button.getAttribute('aria-label')),
    ).toEqual(['Clear filters', null, null, null, null]);
  });
  it('shows an honest disconnected workspace without starting collection', () => {
    render(<App state={{ status: 'disconnected' }} />);
    expect(
      screen.getByRole('heading', { name: 'WaterlooWorks Flash Next' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Backend not connected')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Refresh postings' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('searchbox', {
        name: 'Search titles, companies, locations, and posting IDs',
      }),
    ).toBeDisabled();
    expect(screen.getByText('No posting data loaded')).toBeInTheDocument();
    expect(
      screen.getByRole('navigation', { name: 'Posting pages' }),
    ).toBeInTheDocument();
    for (const label of [
      'Organization',
      'Country',
      'City',
      'Targeted disciplines',
    ]) {
      expect(
        within(
          screen.getByRole('complementary', { name: 'Filters' }),
        ).getByText(label),
      ).toBeInTheDocument();
    }
  });

  it('distinguishes an empty database from a disconnected backend', async () => {
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
    expect(screen.getByText('No stored postings')).toBeInTheDocument();
    expect(
      screen.getByText('0 postings · Page 1/1 · Showing 0 on this page'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Page 1 of 1')).not.toBeInTheDocument();
    expect(
      screen.getByRole('region', { name: 'Posting cards' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Sort by$/ }));
    fireEvent.click(screen.getByRole('option', { name: 'Most applications' }));
    expect(screen.getByRole('button', { name: /Sort by$/ })).toHaveTextContent(
      'Most applications',
    );
    expect(screen.queryByText('Backend not connected')).not.toBeInTheDocument();
    const search = screen.getByRole('searchbox', {
      name: 'Search titles, companies, locations, and posting IDs',
    });
    expect(search).toBeEnabled();
    fireEvent.change(search, { target: { value: 'search' } });
    expect(await screen.findByText('No matching postings')).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Clear search and filters' }),
    );
    expect(search).toHaveValue('');
    expect(
      screen.getByText('No current country predictions yet.'),
    ).toBeInTheDocument();
  });

  it('hides an inactive console and exposes no individual posting views', () => {
    render(<App state={{ status: 'disconnected' }} />);
    expect(screen.queryByRole('log')).not.toBeInTheDocument();
    expect(screen.queryByText(/Refresh log/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole('tab', { name: 'Listing history' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'View listing' }),
    ).not.toBeInTheDocument();
  });

  it('toggles and restores only the theme preference', () => {
    const first = render(<App state={{ status: 'disconnected' }} />);
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
    // A single-select toggle group, not a tablist whose tabs control no panel.
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
    const group = screen.getByRole('radiogroup', { name: 'Color theme' });
    expect(within(group).getByRole('radio', { name: 'System' })).toBeChecked();
    for (const name of ['System', 'Light', 'Dark']) {
      const button = within(group).getByRole('radio', { name });
      expect(button.querySelector('svg')).not.toBeNull();
      expect(button).not.toHaveTextContent(name);
    }
    fireEvent.click(screen.getByRole('radio', { name: 'Dark' }));
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(localStorage.getItem('heroui-theme')).toBe('dark');
    // Selecting the current choice again keeps one option selected.
    fireEvent.click(screen.getByRole('radio', { name: 'Dark' }));
    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked();
    expect(localStorage.getItem('heroui-theme')).toBe('dark');
    first.unmount();
    render(<App state={{ status: 'disconnected' }} />);
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked();
    fireEvent.click(screen.getByRole('radio', { name: 'Light' }));
    expect(document.documentElement).toHaveAttribute('data-theme', 'light');
    expect(document.documentElement).not.toHaveClass('dark');
    expect(localStorage.getItem('heroui-theme')).toBe('light');
    fireEvent.click(screen.getByRole('radio', { name: 'System' }));
    expect(localStorage.getItem('heroui-theme')).toBe('system');
  });

  it('mirrors the card header, identity, divider and five detail rows while loading', () => {
    const { container } = render(<App state={{ status: 'loading' }} />);
    const cards = container.querySelectorAll('[data-posting-skeleton]');
    expect(cards).toHaveLength(18);
    for (const card of cards) {
      expect(
        card.querySelector('[data-slot="card-header"] .posting-title'),
      ).not.toBeNull();
      expect(card.querySelector('.posting-identity')).not.toBeNull();
      expect(card.querySelector('[role="separator"]')).not.toBeNull();
      expect(
        card.querySelectorAll('.posting-details .posting-field'),
      ).toHaveLength(5);
      expect(card.querySelectorAll('.posting-field')).toHaveLength(7);
    }
  });
  it('does not misreport loading or errors as an empty database', () => {
    const { rerender } = render(<App state={{ status: 'loading' }} />);
    expect(screen.getByText('Loading saved postings…')).toBeInTheDocument();
    expect(
      screen.getByRole('region', { name: 'Posting browser' }),
    ).toHaveAttribute('aria-busy', 'true');
    expect(screen.queryByText('Loading workspace')).not.toBeInTheDocument();
    expect(
      screen.queryByText('No posting data loaded'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText('Connect the local API to browse your postings.'),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('navigation', { name: 'Posting pages' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('article')).not.toBeInTheDocument();
    expect(screen.queryByText('No stored postings')).not.toBeInTheDocument();
    rerender(<App state={{ status: 'error' }} />);
    expect(screen.getByText('Workspace unavailable')).toBeInTheDocument();
    expect(screen.queryByText('No stored postings')).not.toBeInTheDocument();
  });
});
