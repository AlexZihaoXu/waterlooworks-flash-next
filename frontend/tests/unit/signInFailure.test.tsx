import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AuthGate } from '../../src/app/AuthenticatedApp';
import {
  AuthHttpError,
  classifySignInError,
} from '../../src/lib/authLifecycle';

// Error shapes only (Firebase error codes and HTTP statuses); no tokens or
// provider responses are fabricated.
describe('sign-in failure classification', () => {
  it.each([
    'auth/popup-closed-by-user',
    'auth/cancelled-popup-request',
    'auth/user-cancelled',
  ])('treats %s as a neutral cancellation', (code) => {
    expect(classifySignInError(Object.assign(new Error('x'), { code }))).toBe(
      'cancelled',
    );
  });
  it('treats a rejected exchange as an account that is not authorized', () => {
    expect(classifySignInError(new AuthHttpError('exchange', 401))).toBe(
      'unauthorized',
    );
    expect(classifySignInError(new AuthHttpError('exchange', 403))).toBe(
      'unauthorized',
    );
  });
  it('keeps every other failure as service unavailable', () => {
    expect(classifySignInError(new AuthHttpError('exchange', 429))).toBe(
      'unavailable',
    );
    expect(classifySignInError(new AuthHttpError('exchange', 503))).toBe(
      'unavailable',
    );
    expect(classifySignInError(new AuthHttpError('prepare', 403))).toBe(
      'unavailable',
    );
    expect(
      classifySignInError(
        Object.assign(new Error('x'), { code: 'auth/network-request-failed' }),
      ),
    ).toBe('unavailable');
    expect(classifySignInError(new TypeError('Failed to fetch'))).toBe(
      'unavailable',
    );
    expect(classifySignInError(undefined)).toBe('unavailable');
  });
  it('never echoes server or SDK error text in its message', () => {
    expect(new AuthHttpError('exchange', 401).message).toBe(
      'Authentication unavailable.',
    );
  });
});

describe('sign-in failure presentation', () => {
  const gate = (reason?: 'cancelled' | 'unauthorized' | 'unavailable') =>
    render(
      <AuthGate state={{ status: 'error', reason }} signIn={() => {}}>
        <p>Protected workspace</p>
      </AuthGate>,
    );
  it('shows a cancelled popup without an alarming alert', () => {
    gate('cancelled');
    expect(screen.getByText('Sign-in cancelled')).toBeInTheDocument();
    expect(document.querySelector('.alert--danger')).toBeNull();
    expect(document.querySelector('.alert--warning')).toBeNull();
    expect(
      screen.queryByText('Authentication unavailable'),
    ).not.toBeInTheDocument();
  });
  it('tells an unapproved account to ask the administrator', () => {
    gate('unauthorized');
    const alert = screen
      .getByText('Account not authorized')
      .closest('.alert') as HTMLElement;
    expect(alert).toHaveClass('alert--warning');
    expect(alert).toHaveTextContent(/administrator/);
  });
  it.each([undefined, 'unavailable'] as const)(
    'keeps service failures as a danger alert (%s)',
    (reason) => {
      gate(reason);
      expect(
        screen.getByText('Authentication unavailable').closest('.alert'),
      ).toHaveClass('alert--danger');
    },
  );
});
