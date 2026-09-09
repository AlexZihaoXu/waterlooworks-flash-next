import { useEffect } from 'react';
import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AuthGate } from '../../src/app/AuthenticatedApp';

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem('ww-pay-preferences');
});

describe('AuthGate', () => {
  it.each(['checking', 'signed-out', 'refreshing', 'error'] as const)(
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
        <AuthGate
          state={
            status === 'refreshing'
              ? {
                  status,
                  session: {
                    uid: 'internal-uid',
                    role: 'member',
                    expires_at: 1,
                  },
                }
              : { status }
          }
          signIn={() => {}}
        >
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
        screen.getByRole('tablist', { name: 'Color theme' }),
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
});
