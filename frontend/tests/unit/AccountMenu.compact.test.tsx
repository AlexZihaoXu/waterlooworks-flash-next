import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AccountMenu } from '../../src/app/AuthenticatedApp';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// Placeholder identity text only; no real account data.
const profile = {
  display_name: 'Placeholder Name',
  email: 'placeholder@example.invalid',
  photo_url: null,
};

describe('phone account menu', () => {
  it('shows only a 44px avatar trigger and moves name and email into the menu', async () => {
    vi.stubGlobal('innerWidth', 390);
    const signOut = vi.fn();
    render(<AccountMenu profile={profile} signOut={signOut} />);
    const trigger = screen.getByRole('button', { name: 'Account menu' });
    expect(trigger).toHaveClass('button--lg', 'button--icon-only');
    expect(trigger).not.toHaveTextContent(profile.email);
    expect(trigger).not.toHaveTextContent(profile.display_name);
    fireEvent.click(trigger);
    const signOutItem = await screen.findByRole('menuitem', {
      name: 'Sign out',
    });
    expect(screen.getByText(profile.email)).toBeInTheDocument();
    expect(screen.getByText(profile.display_name)).toBeInTheDocument();
    fireEvent.click(signOutItem);
    expect(signOut).toHaveBeenCalledOnce();
  });

  it('keeps name and email on the trigger on wider screens', () => {
    vi.stubGlobal('innerWidth', 1024);
    render(<AccountMenu profile={profile} signOut={() => {}} />);
    const trigger = screen.getByRole('button', { name: 'Account menu' });
    expect(trigger).toHaveTextContent(profile.email);
    expect(trigger).toHaveTextContent(profile.display_name);
  });

  it('lets a phone admin open Admin settings or sign out from the menu', async () => {
    vi.stubGlobal('innerWidth', 390);
    // A pending transport only: no fabricated API response.
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise(() => {}),
    );
    render(
      <AccountMenu
        profile={profile}
        signOut={() => {}}
        role="admin"
        uid="admin-a"
      />,
    );
    const trigger = screen.getByRole('button', { name: 'Account menu' });
    expect(trigger).not.toHaveTextContent(profile.email);
    fireEvent.click(trigger);
    const menu = await screen.findByRole('menu');
    expect(
      within(menu).getByRole('menuitem', { name: 'Sign out' }),
    ).toBeInTheDocument();
    fireEvent.click(
      within(menu).getByRole('menuitem', { name: 'Admin settings' }),
    );
    expect(
      await screen.findByRole('dialog', { name: 'Admin settings' }),
    ).toBeInTheDocument();
  });
});
