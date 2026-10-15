import { readFileSync } from 'node:fs';
import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from '../../src/app/App';
import type { WorkspaceState } from '../../src/lib/workspace';
import { refreshSnapshot } from '../support/refreshSnapshot';

afterEach(() => vi.unstubAllGlobals());

// An empty ready database and a contract-default running snapshot only.
const empty: WorkspaceState = {
  status: 'ready',
  postings: [],
  manual_refresh_available: true,
  details_available: false,
  compensation_sort_available: true,
};
const running = refreshSnapshot({
  run_id: 'run-1',
  status: 'running',
  stage: 'listings',
  pages_completed: 1,
  total_pages: 4,
});

describe('phone top app bar', () => {
  it('keeps header, refresh status and the controls row in one top bar', () => {
    vi.stubGlobal('innerWidth', 390);
    render(
      <App state={empty} refresh={running} onOpenAdminSettings={() => {}} />,
    );
    const bar = screen.getByRole('banner').closest('.app-bar') as HTMLElement;
    expect(bar).not.toBeNull();
    expect(bar).toContainElement(
      screen.getByRole('searchbox', {
        name: 'Search titles, companies, locations, and posting IDs',
      }),
    );
    expect(bar).toContainElement(
      screen.getByRole('button', { name: 'Refresh details' }),
    );
    // Only the postings scroll beneath the bar.
    expect(bar).not.toContainElement(
      screen.getByRole('region', { name: 'Posting cards' }),
    );
  });

  it('shows its separator only once content scrolls beneath it', () => {
    vi.stubGlobal('innerWidth', 390);
    render(<App state={empty} />);
    const bar = screen.getByRole('banner').closest('.app-bar') as HTMLElement;
    expect(bar).toHaveAttribute('data-scrolled', 'false');
    act(() => {
      vi.stubGlobal('scrollY', 240);
      window.dispatchEvent(new Event('scroll'));
    });
    expect(bar).toHaveAttribute('data-scrolled', 'true');
    act(() => {
      vi.stubGlobal('scrollY', 0);
      window.dispatchEvent(new Event('scroll'));
    });
    expect(bar).toHaveAttribute('data-scrolled', 'false');
  });

  it('pins an opaque, safe-area-aware bar and offsets page scrolling under it', () => {
    const css = readFileSync('src/styles/index.css', 'utf8');
    const bar = /\.app-bar\s*\{([^}]*)\}/.exec(css)![1];
    expect(bar).toMatch(/position:\s*sticky/);
    expect(bar).toMatch(/top:\s*0/);
    expect(bar).toMatch(/background-color:\s*var\(--background\)/);
    expect(bar).toMatch(/env\(safe-area-inset-top\)/);
    expect(css).toMatch(
      /\.app-bar\[data-scrolled='true'\]\s*\{[^}]*border-bottom-color/,
    );
    expect(css).toMatch(/scroll-padding-top:\s*var\(--app-bar-height/);
  });

  it('keeps the existing layout without a top bar on wider screens', () => {
    vi.stubGlobal('innerWidth', 1024);
    render(<App state={empty} />);
    expect(screen.getByRole('banner').closest('.app-bar')).toBeNull();
  });
});
