import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AuthGate } from '../../src/app/AuthenticatedApp';
import {
  AuthHttpError,
  classifySignInError,
  parseRefusalCode,
  type SignInFailure,
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
  it('separates a rejected exchange from the empty-instance owner pin', () => {
    expect(classifySignInError(new AuthHttpError('exchange', 401))).toBe(
      'rejected',
    );
    expect(classifySignInError(new AuthHttpError('exchange', 403))).toBe(
      'owner-required',
    );
    expect(
      classifySignInError(
        new AuthHttpError('exchange', 403, null, 'owner_required'),
      ),
    ).toBe('owner-required');
  });
  it('reads the fixed admission refusal codes and ignores anything else', () => {
    expect(
      classifySignInError(
        new AuthHttpError('exchange', 403, null, 'account_held'),
      ),
    ).toBe('held');
    expect(
      classifySignInError(
        new AuthHttpError('exchange', 403, null, 'not_approved'),
      ),
    ).toBe('not-approved');
    expect(parseRefusalCode({ detail: 'x', code: 'not_approved' })).toBe(
      'not_approved',
    );
    expect(parseRefusalCode({ detail: 'x', code: 'banned' })).toBeNull();
    expect(parseRefusalCode({ detail: 'x' })).toBeNull();
    expect(parseRefusalCode('not_approved')).toBeNull();
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
  const gate = (reason?: SignInFailure) =>
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
  it('asks a rejected sign-in to retry without promising an approval flow', () => {
    gate('rejected');
    const alert = screen
      .getByText('Sign-in not accepted')
      .closest('.alert') as HTMLElement;
    expect(alert).toHaveClass('alert--warning');
    expect(alert).toHaveTextContent(/try again/i);
    expect(alert).not.toHaveTextContent(/approve/i);
  });
  it('explains that only the configured owner can claim an empty instance', () => {
    gate('owner-required');
    const alert = screen
      .getByText('Owner sign-in required')
      .closest('.alert') as HTMLElement;
    expect(alert).toHaveClass('alert--warning');
    expect(alert).toHaveTextContent(/configured owner/);
    expect(alert).not.toHaveTextContent(/approve/i);
  });
  it('tells a declined or removed account it is not approved', () => {
    gate('not-approved');
    const alert = screen
      .getByText('Not approved')
      .closest('.alert') as HTMLElement;
    expect(alert).toHaveClass('alert--warning');
    expect(alert).toHaveTextContent(/not been approved/i);
    expect(alert).not.toHaveTextContent(/try again/i);
  });
  it('explains a held instance without inviting a retry loop', () => {
    gate('held');
    const alert = screen
      .getByText('New accounts are on hold')
      .closest('.alert') as HTMLElement;
    expect(alert).toHaveClass('alert--warning');
    expect(alert).toHaveTextContent(/not accepting new accounts/i);
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
