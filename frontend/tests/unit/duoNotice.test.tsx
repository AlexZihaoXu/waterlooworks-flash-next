import { createRef } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from '../../src/app/App';
import {
  AccountMenu,
  type AdminSettingsHandle,
} from '../../src/app/AuthenticatedApp';
import type { WorkspaceState } from '../../src/lib/workspace';
import { refreshSnapshot } from '../support/refreshSnapshot';

afterEach(() => vi.restoreAllMocks());

const empty: WorkspaceState = {
  status: 'ready',
  postings: [],
  manual_refresh_available: true,
  details_available: false,
  compensation_sort_available: true,
};
const authenticating = refreshSnapshot({
  run_id: 'run-1',
  status: 'running',
  stage: 'authentication',
});

describe('workspace Duo notice', () => {
  it('tells the admin that Duo approval may be pending and opens settings', () => {
    const open = vi.fn();
    render(
      <App state={empty} refresh={authenticating} onOpenAdminSettings={open} />,
    );
    const alert = screen
      .getByText('Duo approval may be required')
      .closest('.alert') as HTMLElement;
    expect(alert).toHaveClass('alert--warning');
    expect(alert).toHaveTextContent(/3 minutes/);
    // The workspace never shows the verification code itself.
    expect(alert).not.toHaveTextContent(/\b\d{3}\b/);
    fireEvent.click(
      screen.getByRole('button', { name: 'Open Admin settings' }),
    );
    expect(open).toHaveBeenCalledOnce();
  });
  it('shows nothing to members or outside the authentication stage', () => {
    const { rerender } = render(<App state={empty} refresh={authenticating} />);
    expect(
      screen.queryByText('Duo approval may be required'),
    ).not.toBeInTheDocument();
    rerender(
      <App
        state={empty}
        refresh={refreshSnapshot({
          run_id: 'run-1',
          status: 'running',
          stage: 'listings',
        })}
        onOpenAdminSettings={() => {}}
      />,
    );
    expect(
      screen.queryByText('Duo approval may be required'),
    ).not.toBeInTheDocument();
  });
  it('lets the workspace open Admin settings directly on the Refresh tab', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise(() => {}),
    );
    const handle = createRef<AdminSettingsHandle>();
    render(
      <AccountMenu
        profile={null}
        signOut={() => {}}
        role="admin"
        uid="admin-a"
        settingsRef={handle}
      />,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    act(() => handle.current!.open());
    expect(await screen.findByRole('tab', { name: 'Refresh' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByText('Current Duo approval')).toBeInTheDocument();
  });
});
