import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AccountMenu } from '../../src/app/AuthenticatedApp';

describe('AccountMenu HeroUI composition', () => {
  it('shows only generic Account without inventing a name, email or role chip', async () => {
    const signOut = vi.fn();
    render(<AccountMenu profile={null} signOut={signOut} />);
    expect(
      screen.getByRole('button', { name: 'Account menu' }),
    ).toHaveTextContent('Account');
    expect(screen.queryByText(/Admin|Member|@/)).not.toBeInTheDocument();
    expect(screen.queryByRole('menuitem')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Account menu' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Sign out' }));
    expect(signOut).toHaveBeenCalledOnce();
  });

  it('opens from the keyboard and activates Sign out with Enter', async () => {
    const signOut = vi.fn();
    render(<AccountMenu profile={null} signOut={signOut} />);
    const trigger = screen.getByRole('button', { name: 'Account menu' });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    fireEvent.keyUp(trigger, { key: 'ArrowDown' });
    // Settings comes first; ArrowDown moves on to Sign out.
    const settings = await screen.findByRole('menuitem', { name: 'Settings' });
    await waitFor(() => expect(settings).toHaveFocus());
    fireEvent.keyDown(settings, { key: 'ArrowDown' });
    fireEvent.keyUp(settings, { key: 'ArrowDown' });
    const item = screen.getByRole('menuitem', { name: 'Sign out' });
    await waitFor(() => expect(item).toHaveFocus());
    fireEvent.keyDown(item, { key: 'Enter' });
    fireEvent.keyUp(item, { key: 'Enter' });
    expect(signOut).toHaveBeenCalledOnce();
  });
});
