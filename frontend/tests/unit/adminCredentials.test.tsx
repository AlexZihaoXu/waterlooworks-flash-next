import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AccountMenu } from '../../src/app/AuthenticatedApp';
import { useAdminCredentials } from '../../src/lib/useAdminCredentials';

// Empty metadata and random input primitives only, never provider/posting fixtures.
const emptyStatus = {
  configured: false,
  status: 'not_configured',
  updated_at: null,
  username: null,
};
afterEach(() => vi.restoreAllMocks());

describe('admin credentials settings', () => {
  it('opens admin settings directly; members have only the sign-out menu and no settings reads', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(Response.json(emptyStatus));
    const signOut = vi.fn();
    const view = render(<AccountMenu profile={null} signOut={signOut} />);
    fireEvent.click(screen.getByRole('button', { name: 'Account menu' }));
    expect(
      await screen.findByRole('menuitem', { name: 'Sign out' }),
    ).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
    view.unmount();
    render(
      <AccountMenu
        profile={null}
        signOut={signOut}
        role="admin"
        uid="admin-a"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Account menu' }));
    expect(
      await screen.findByRole('dialog', { name: 'Admin settings' }),
    ).toBeInTheDocument();
    expect(screen.queryByRole('menuitem')).not.toBeInTheDocument();
    const username = screen.getByLabelText('WaterlooWorks username');
    const password = screen.getByLabelText('WaterlooWorks password');
    expect(username).toHaveAttribute('autocomplete', 'off');
    expect(password).toHaveAttribute('autocomplete', 'new-password');
    fireEvent.change(username, { target: { value: crypto.randomUUID() } });
    fireEvent.change(password, { target: { value: crypto.randomUUID() } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(signOut).toHaveBeenCalledOnce();
    // Immediate shell unmount replaces HeroUI's exit animation. Assert removal
    // and cleared hook state on reopen, not the value of a detached DOM node.
    expect(username).not.toBeInTheDocument();
    expect(password).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Account menu' }));
    expect(await screen.findByLabelText('WaterlooWorks username')).toHaveValue(
      '',
    );
    expect(screen.getByLabelText('WaterlooWorks password')).toHaveValue('');
  });

  it('clears both fields on success/close/account change and fences late writes', async () => {
    const pending: Array<{
      signal: AbortSignal;
      resolve: (response: Response) => void;
    }> = [];
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(
        (_url, options) =>
          new Promise((resolve) =>
            pending.push({ signal: options!.signal!, resolve }),
          ),
      );
    const { result, rerender } = renderHook(
      ({ uid }) => useAdminCredentials(uid),
      { initialProps: { uid: 'admin-a' } },
    );
    act(() => result.current.setOpen(true));
    await waitFor(() => expect(pending).toHaveLength(1));
    await act(async () => pending[0].resolve(Response.json(emptyStatus)));
    const first = crypto.randomUUID();
    act(() => {
      result.current.setUsername(first);
      result.current.setPassword(crypto.randomUUID());
    });
    act(() => {
      void result.current.save();
    });
    await waitFor(() => expect(pending).toHaveLength(2));
    expect(fetch.mock.calls[1][1]).toMatchObject({
      method: 'POST',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', 'X-WW-Request': '1' },
    });
    act(() => result.current.setOpen(false));
    expect(pending[1].signal.aborted).toBe(true);
    expect(result.current.username).toBe('');
    expect(result.current.password).toBe('');
    act(() => result.current.setOpen(true));
    await waitFor(() => expect(pending).toHaveLength(3));
    await act(async () =>
      pending[1].resolve(
        Response.json({
          configured: true,
          status: 'configured',
          updated_at: new Date().toISOString(),
        }),
      ),
    );
    expect(result.current.message).not.toContain('Saved');
    await act(async () => pending[2].resolve(Response.json(emptyStatus)));
    act(() => {
      result.current.setUsername(crypto.randomUUID());
      result.current.setPassword(crypto.randomUUID());
    });
    act(() => {
      void result.current.save();
    });
    await waitFor(() => expect(pending).toHaveLength(4));
    await act(async () =>
      pending[3].resolve(
        Response.json({
          configured: true,
          status: 'configured',
          updated_at: new Date().toISOString(),
        }),
      ),
    );
    expect(result.current.username).toBe('');
    expect(result.current.password).toBe('');
    expect(result.current.message).toContain('Saved');
    act(() => {
      result.current.setUsername(crypto.randomUUID());
      result.current.setPassword(crypto.randomUUID());
    });
    rerender({ uid: 'admin-b' });
    expect(result.current.username).toBe('');
    expect(result.current.password).toBe('');
    expect(result.current.isOpen).toBe(false);
  });
});

describe('admin settings request boundaries', () => {
  it('bounds requests even when transport ignores cancellation and uses fixed safe errors', async () => {
    vi.useFakeTimers();
    try {
      const fetch = vi
        .spyOn(globalThis, 'fetch')
        .mockImplementation(() => new Promise(() => {}));
      const saveStorage = vi.spyOn(Storage.prototype, 'setItem');
      const { result, unmount } = renderHook(() =>
        useAdminCredentials('admin-a'),
      );
      act(() => result.current.setOpen(true));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(fetch).toHaveBeenCalledOnce();
      const signal = fetch.mock.calls[0][1]!.signal!;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(15_000);
      });
      expect(signal.aborted).toBe(true);
      expect(result.current.pending).toBe(false);
      expect(result.current.message).toBe(
        'Credential settings unavailable. Check your session and the backend encryption key.',
      );
      expect(saveStorage).not.toHaveBeenCalled();
      unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it('Escape asks first, then a confirmed discard closes and clears both input fields before reopening from the keyboard', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
      Promise.resolve(Response.json(emptyStatus)),
    );
    render(
      <AccountMenu
        profile={null}
        signOut={() => {}}
        role="admin"
        uid="admin-a"
      />,
    );
    const trigger = screen.getByRole('button', { name: 'Account menu' });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: 'Enter', code: 'Enter' });
    fireEvent.keyUp(trigger, { key: 'Enter', code: 'Enter' });
    await screen.findByText('Credentials not configured.');
    fireEvent.change(screen.getByLabelText('WaterlooWorks username'), {
      target: { value: crypto.randomUUID() },
    });
    fireEvent.change(screen.getByLabelText('WaterlooWorks password'), {
      target: { value: crypto.randomUUID() },
    });
    // Native Escape dispatches cancel; jsdom has no dialog default actions.
    fireEvent(
      screen.getByRole('dialog'),
      new Event('cancel', { cancelable: true }),
    );
    // Non-empty drafts are not dropped by a stray Escape.
    fireEvent.click(
      await screen.findByRole('button', { name: 'Discard changes' }),
    );
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
    fireEvent.click(trigger);
    expect(await screen.findByLabelText('WaterlooWorks username')).toHaveValue(
      '',
    );
    expect(screen.getByLabelText('WaterlooWorks password')).toHaveValue('');
  });

  it('aborts pending reads on account change and never allows late data to restore state', async () => {
    let resolve!: (response: Response) => void;
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    const { result, rerender } = renderHook(
      ({ uid }) => useAdminCredentials(uid),
      { initialProps: { uid: 'admin-a' } },
    );
    act(() => result.current.setOpen(true));
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    rerender({ uid: 'admin-b' });
    expect(fetch.mock.calls[0][1]!.signal!.aborted).toBe(true);
    await act(async () => resolve(Response.json(emptyStatus)));
    expect(result.current.isOpen).toBe(false);
    expect(result.current.message).toBe('');
  });
});

it('shows the authorized saved username, never populates password input or submits the fixed mask', async () => {
  const savedUsername = crypto.randomUUID();
  const configured = {
    configured: true,
    status: 'configured',
    updated_at: new Date().toISOString(),
    username: savedUsername,
  };
  const fetch = vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation((url) =>
      Promise.resolve(
        Response.json(
          String(url).includes('waterlooworks-credentials')
            ? configured
            : { configured: false, status: 'not_configured', updated_at: null },
        ),
      ),
    );
  render(
    <AccountMenu
      profile={null}
      signOut={() => {}}
      role="admin"
      uid="admin-a"
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Account menu' }));
  await screen.findByText(`Saved username/email: ${savedUsername}`);
  const password = screen.getByLabelText('WaterlooWorks password');
  expect(password).toHaveValue('');
  expect(password).toHaveAttribute('placeholder', '••••••••');
  expect(screen.getByLabelText('WaterlooWorks username')).toHaveValue('');
  fireEvent.change(screen.getByLabelText('WaterlooWorks username'), {
    target: { value: crypto.randomUUID() },
  });
  fireEvent.change(password, { target: { value: '••••••••' } });
  expect(
    screen.getByRole('button', { name: 'Save credentials' }),
  ).toBeDisabled();
  fireEvent.submit(
    screen.getByRole('form', { name: 'WaterlooWorks credentials' }),
  );
  expect(
    fetch.mock.calls.every(([, options]) => options?.method === 'GET'),
  ).toBe(true);
  expect(
    screen.queryByRole('button', { name: /reveal.*password/i }),
  ).not.toBeInTheDocument();
});
