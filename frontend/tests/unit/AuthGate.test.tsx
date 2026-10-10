import { useEffect } from 'react';
import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AuthGate } from '../../src/app/AuthenticatedApp';

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem('ww-pay-preferences');
});

describe('AuthGate', () => {
  it.each(['checking', 'signed-out', 'error'] as const)(
    'shows the real shell but never starts a protected reader while %s',
    async (status) => {
      const fetch = vi.spyOn(globalThis, 'fetch');
      const reader = vi.fn(() => <p>Protected workspace</p>);
      const Reader = reader;
      window.history.replaceState(null, '', '/?job=1');
      localStorage.setItem(
        'ww-pay-preferences',
        JSON.stringify({ currency: 'USD' }),
      );
      render(
        <AuthGate state={{ status }} signIn={() => {}}>
          <Reader />
        </AuthGate>,
      );
      await act(async () => {
        await Promise.resolve();
      });
      expect(reader).not.toHaveBeenCalled();
      expect(fetch).not.toHaveBeenCalled();
      expect(screen.queryByText('Protected workspace')).not.toBeInTheDocument();
      expect(screen.getByText('Sign in to continue')).toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: 'Sign in with Google' }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole('radiogroup', { name: 'Color theme' }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole('searchbox', {
          name: 'Search titles, companies, locations, and posting IDs',
        }),
      ).toBeDisabled();
      expect(
        screen.getByText('Waiting for saved postings'),
      ).toBeInTheDocument();
      expect(screen.queryByText('No stored postings')).not.toBeInTheDocument();
      expect(document.querySelector('[data-posting-skeleton]')).toBeNull();
    },
  );
  it('unmounts the protected subtree on logout rather than merely hiding it', () => {
    const cleared = vi.fn();
    function ProtectedReader() {
      useEffect(() => cleared, []);
      return <p>Protected workspace</p>;
    }
    // Application-session primitives, not a fabricated Firebase/provider response.
    const { rerender } = render(
      <AuthGate
        state={{
          status: 'ready',
          session: { uid: 'internal-uid', role: 'member', expires_at: 1 },
        }}
        signIn={() => {}}
      >
        <ProtectedReader />
      </AuthGate>,
    );
    expect(screen.getByText('Protected workspace')).toBeInTheDocument();
    rerender(
      <AuthGate state={{ status: 'signed-out' }} signIn={() => {}}>
        <ProtectedReader />
      </AuthGate>,
    );
    expect(cleared).toHaveBeenCalledOnce();
    expect(screen.queryByText('Protected workspace')).not.toBeInTheDocument();
  });
  it('keeps the protected workspace mounted while the session renews', () => {
    const mounted = vi.fn();
    const cleared = vi.fn();
    function ProtectedReader() {
      useEffect(() => {
        mounted();
        return cleared;
      }, []);
      return <p>Protected workspace</p>;
    }
    // Application-session primitives, not a fabricated Firebase/provider response.
    const session = {
      uid: 'internal-uid',
      role: 'member',
      expires_at: 1,
    } as const;
    const { rerender } = render(
      <AuthGate state={{ status: 'ready', session }} signIn={() => {}}>
        <ProtectedReader />
      </AuthGate>,
    );
    rerender(
      <AuthGate state={{ status: 'refreshing', session }} signIn={() => {}}>
        <ProtectedReader />
      </AuthGate>,
    );
    expect(screen.getByText('Protected workspace')).toBeInTheDocument();
    expect(screen.queryByText('Sign in to continue')).not.toBeInTheDocument();
    rerender(
      <AuthGate
        state={{ status: 'ready', session: { ...session, expires_at: 2 } }}
        signIn={() => {}}
      >
        <ProtectedReader />
      </AuthGate>,
    );
    expect(mounted).toHaveBeenCalledOnce();
    expect(cleared).not.toHaveBeenCalled();
  });
  it('explains a session that ended because renewal failed', () => {
    render(
      <AuthGate
        state={{ status: 'signed-out', notice: 'session-ended' }}
        signIn={() => {}}
      >
        <p>Protected workspace</p>
      </AuthGate>,
    );
    const alert = screen
      .getByText('Session ended')
      .closest('.alert') as HTMLElement;
    expect(alert).toHaveClass('alert--warning');
    expect(screen.getByText('Sign in to continue')).toBeInTheDocument();
  });
  it('shows no notice after an explicit sign-out', () => {
    render(
      <AuthGate state={{ status: 'signed-out' }} signIn={() => {}}>
        <p>Protected workspace</p>
      </AuthGate>,
    );
    expect(screen.queryByText('Session ended')).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
