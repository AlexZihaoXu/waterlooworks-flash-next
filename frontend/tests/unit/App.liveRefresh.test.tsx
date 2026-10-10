import { render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { App } from '../../src/app/App';
import * as workspace from '../../src/lib/workspace';
import type { WorkspaceState } from '../../src/lib/workspace';
import { refreshSnapshot } from '../support/refreshSnapshot';

// Wrap (not replace) a real helper that the browser body calls on every render.
vi.mock(import('../../src/lib/workspace'), async (original) => {
  const actual = await original();
  return { ...actual, pageNumbers: vi.fn(actual.pageNumbers) };
});

afterEach(() => vi.mocked(workspace.pageNumbers).mockClear());

// An empty ready database, not a posting fixture.
const empty: WorkspaceState = {
  status: 'ready',
  postings: [],
  manual_refresh_available: false,
  details_available: false,
  compensation_sort_available: true,
};

it('updates live refresh progress without re-rendering the posting browser', () => {
  const running = (pages: number) =>
    refreshSnapshot({
      run_id: 'run-1',
      status: 'running',
      stage: 'listings',
      pages_completed: pages,
      total_pages: 10,
    });
  const { rerender } = render(<App state={empty} refresh={running(1)} />);
  expect(screen.getByText(/^1 \/ 10 pages/)).toBeInTheDocument();
  const renders = vi.mocked(workspace.pageNumbers).mock.calls.length;
  expect(renders).toBeGreaterThan(0);
  for (const pages of [2, 3, 4])
    rerender(<App state={empty} refresh={running(pages)} />);
  expect(screen.getByText(/^4 \/ 10 pages/)).toBeInTheDocument();
  expect(vi.mocked(workspace.pageNumbers).mock.calls.length).toBe(renders);
});
