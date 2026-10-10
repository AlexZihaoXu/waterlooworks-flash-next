import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { App } from '../../src/app/App';

afterEach(() => vi.unstubAllGlobals());

it('keeps picker actions in a wrapping native layout', async () => {
  window.history.replaceState(null, '', '/?target=unknown');
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
  fireEvent.click(
    screen.getByRole('button', {
      name: 'Targeted disciplines unknown',
    }),
  );
  const apply = await screen.findByRole('button', { name: 'Apply filters' });
  const layout = apply.parentElement!;
  expect(layout.tagName).toBe('DIV');
  expect(layout).toHaveClass('inline-layout');
  expect(layout).toHaveStyle({ width: '100%', justifyContent: 'flex-end' });
  for (const name of ['Clear selection', 'Cancel']) {
    expect(screen.getByRole('button', { name }).parentElement).toBe(layout);
  }
});

it.each([1440, 320])(
  'mounts loading at the settled listing geometry at %ipx',
  (width) => {
    vi.stubGlobal('innerWidth', width);
    window.history.replaceState(
      null,
      '',
      '/?q=preserve&page=2&sort=applications',
    );
    const search = window.location.search;
    const { container, rerender } = render(
      <App state={{ status: 'loading' }} />,
    );
    const layout = container.querySelector<HTMLElement>('.browser-layout')!;
    expect(layout.style.gridTemplateColumns).toBe(
      width > 896 ? '16rem minmax(0, 1fr)' : 'minmax(0, 1fr)',
    );
    expect(layout.style.columnGap).toBe('1.5rem');
    // Phones open filters from the search row, so no sidebar is mounted.
    expect(container.querySelector('.filter-sidebar') === null).toBe(
      width <= 640,
    );
    for (const selector of [
      '.browse-search',
      ...(width > 640 ? ['.filter-sidebar'] : []),
      '.posting-results',
    ]) {
      expect(
        container.querySelector<HTMLElement>(selector)!.style.opacity,
      ).toBe('1');
    }
    expect(window.location.search).toBe(search);
    const geometry = layout.style.cssText;
    rerender(
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
    expect(layout.style.cssText).toBe(geometry);
  },
);

it.each([1440, 320])(
  'mounts a direct job URL with full-width detail loading and no listing flash at %ipx',
  (width) => {
    vi.stubGlobal('innerWidth', width);
    // A pending transport only: no fabricated API response or posting data.
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    );
    window.history.replaceState(
      null,
      '',
      '/?job=0&q=preserve&page=2&city=preserve&sort=applications',
    );
    const search = window.location.search;
    const { container } = render(<App state={{ status: 'loading' }} />);
    const layout = container.querySelector<HTMLElement>('.browser-layout')!;
    expect(layout.style.gridTemplateColumns).toBe(
      width > 896 ? '0rem minmax(0, 1fr)' : 'minmax(0, 1fr)',
    );
    expect(layout.style.columnGap).toBe('0rem');
    for (const selector of [
      '.browse-search',
      ...(width > 640 ? ['.filter-sidebar'] : []),
      '.posting-results',
    ]) {
      const wrapper = container.querySelector<HTMLElement>(selector)!;
      expect(wrapper.style.opacity).toBe('0');
      expect(wrapper.style.visibility).toBe('hidden');
      expect(wrapper).toHaveAttribute('inert');
    }
    expect(
      screen.getByRole('status', { name: 'Loading stored job details' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('region', { name: 'Job details' }).style.opacity,
    ).toBe('1');
    expect(
      screen.queryByRole('region', { name: 'Posting browser' }),
    ).not.toBeInTheDocument();
    expect(window.location.search).toBe(search);
  },
);
