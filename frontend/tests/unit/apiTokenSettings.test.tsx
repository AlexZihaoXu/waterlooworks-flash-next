import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AccountMenu } from '../../src/app/AuthenticatedApp';
import { parseIssuedToken, parseTokenStatus } from '../../src/lib/useApiToken';

// A syntactically valid placeholder, never a real credential.
const placeholder = `wwf_${'x'.repeat(43)}`;
const at = '2026-01-01T00:00:00+00:00';
const none = {
  configured: false,
  hint: null,
  created_at: null,
  last_used_at: null,
};
const active = {
  configured: true,
  hint: 'wwf_…xxxx',
  created_at: at,
  last_used_at: null,
};

type Call = { path: string; method: string; body: unknown };

function fakeServer(
  routes: Record<string, (body: unknown) => Response | object>,
): Call[] {
  const calls: Call[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path, method, body });
    const handler = routes[`${method} ${path}`];
    if (!handler) return new Response('{}', { status: 404 });
    const result = handler(body);
    return result instanceof Response
      ? result
      : new Response(JSON.stringify(result), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
  });
  return calls;
}

function issued() {
  return { token: placeholder, hint: 'wwf_…xxxx', created_at: at };
}

async function openMemberSettings() {
  render(<AccountMenu profile={null} signOut={() => {}} uid="member-a" />);
  fireEvent.click(screen.getByRole('button', { name: 'Account menu' }));
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Settings' }));
  return screen.findByRole('dialog', { name: 'Settings' });
}

function setVisibility(state: 'hidden' | 'visible') {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => state,
  });
  document.dispatchEvent(new Event('visibilitychange'));
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  setVisibility('visible');
});

describe('API token parsing', () => {
  it('accepts only documented metadata and well-formed tokens', () => {
    expect(parseTokenStatus(active)).toEqual(active);
    expect(parseTokenStatus({ configured: false })).toEqual(none);
    expect(() => parseTokenStatus({ ...active, token: placeholder })).toThrow();
    expect(() => parseTokenStatus({ configured: 'yes' })).toThrow();
    expect(parseIssuedToken(issued())).toEqual(issued());
    expect(() =>
      parseIssuedToken({ ...issued(), token: 'wwf_short' }),
    ).toThrow();
    expect(() => parseIssuedToken({ ...issued(), extra: 1 })).toThrow();
  });
});

describe('member settings: API token', () => {
  it('opens Settings for members and shows a new token exactly once', async () => {
    let status: object = none;
    const calls = fakeServer({
      'GET /api/account/api-token': () => status,
      'POST /api/account/api-token/reset': () => {
        status = active;
        return issued();
      },
    });
    const writeText = vi.fn(async () => {});
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
    const dialog = await openMemberSettings();
    expect(dialog.tagName).toBe('DIALOG');
    expect(
      screen.queryByRole('tab', { name: 'Credentials' }),
    ).not.toBeInTheDocument();
    expect(await screen.findByText('No token yet')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Create token' }));
    const field = await screen.findByLabelText('New API token');
    expect(field).toHaveValue(placeholder);
    expect(
      screen.getByText(/won.t be able to see it again/),
    ).toBeInTheDocument();
    expect(calls.find((call) => call.method === 'POST')?.body).toEqual({});
    fireEvent.click(screen.getByRole('button', { name: 'Copy token' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(placeholder));
    expect(await screen.findByText('Copied to clipboard.')).toBeInTheDocument();
    expect(screen.getByText('wwf_…xxxx')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hide token' }));
    expect(screen.queryByLabelText('New API token')).not.toBeInTheDocument();
    expect(JSON.stringify({ ...localStorage })).not.toContain(placeholder);
  });

  it('selects the token for manual copy when the clipboard is unavailable', async () => {
    fakeServer({
      'GET /api/account/api-token': () => none,
      'POST /api/account/api-token/reset': () => issued(),
    });
    vi.stubGlobal('navigator', { ...navigator, clipboard: undefined });
    await openMemberSettings();
    fireEvent.click(
      await screen.findByRole('button', { name: 'Create token' }),
    );
    const field = (await screen.findByLabelText(
      'New API token',
    )) as HTMLInputElement;
    fireEvent.click(screen.getByRole('button', { name: 'Copy token' }));
    expect(
      await screen.findByText(/Token selected\. Copy it with/),
    ).toBeInTheDocument();
    expect(field.selectionStart).toBe(0);
    expect(field.selectionEnd).toBe(placeholder.length);
  });

  it('clears the revealed token when the browser tab is hidden', async () => {
    fakeServer({
      'GET /api/account/api-token': () => none,
      'POST /api/account/api-token/reset': () => issued(),
    });
    await openMemberSettings();
    fireEvent.click(
      await screen.findByRole('button', { name: 'Create token' }),
    );
    await screen.findByLabelText('New API token');
    act(() => setVisibility('hidden'));
    expect(screen.queryByLabelText('New API token')).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue(placeholder)).not.toBeInTheDocument();
  });

  it('asks before closing while a new token has not been copied', async () => {
    fakeServer({
      'GET /api/account/api-token': () => none,
      'POST /api/account/api-token/reset': () => issued(),
    });
    await openMemberSettings();
    fireEvent.click(
      await screen.findByRole('button', { name: 'Create token' }),
    );
    await screen.findByLabelText('New API token');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(
      await screen.findByRole('alertdialog', {
        name: 'Close without copying?',
      }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close anyway' }));
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
  });

  it('confirms replacing and revoking an existing token', async () => {
    let status: object = { ...active, last_used_at: at };
    const calls = fakeServer({
      'GET /api/account/api-token': () => status,
      'POST /api/account/api-token/reset': () => issued(),
      'DELETE /api/account/api-token': () => {
        status = none;
        return none;
      },
    });
    await openMemberSettings();
    expect(await screen.findByText('wwf_…xxxx')).toBeInTheDocument();
    expect(screen.getByText(/^Last used /)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reset token…' }));
    expect(
      await screen.findByRole('alertdialog', {
        name: 'Replace your API token?',
      }),
    ).toBeInTheDocument();
    expect(calls.some((call) => call.method === 'POST')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Replace token' }));
    expect(await screen.findByLabelText('New API token')).toHaveValue(
      placeholder,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Hide token' }));
    fireEvent.click(screen.getByRole('button', { name: 'Revoke…' }));
    fireEvent.click(
      await screen.findByRole('button', { name: 'Revoke token' }),
    );
    expect(await screen.findByText('No token yet')).toBeInTheDocument();
    expect(calls.find((call) => call.method === 'DELETE')?.body).toEqual({});
  });

  it('explains the reset rate limit without exposing server text', async () => {
    fakeServer({
      'GET /api/account/api-token': () => none,
      'POST /api/account/api-token/reset': () =>
        new Response(JSON.stringify({ detail: 'internal detail' }), {
          status: 429,
          headers: { 'Retry-After': '540' },
        }),
    });
    await openMemberSettings();
    fireEvent.click(
      await screen.findByRole('button', { name: 'Create token' }),
    );
    expect(
      await screen.findByText(
        'Too many token resets. Try again in about 9 minutes.',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/internal detail/)).not.toBeInTheDocument();
  });
});

describe('admin settings: API token tab', () => {
  it('adds an API token tab and clears a revealed token on leaving it', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === '/api/account/api-token/reset' && init?.method === 'POST')
        return new Response(JSON.stringify(issued()), { status: 200 });
      if (path === '/api/account/api-token')
        return new Response(JSON.stringify(none), { status: 200 });
      // Other admin tabs stay pending: no fabricated settings responses.
      return new Promise<Response>(() => {});
    });
    render(
      <AccountMenu
        profile={null}
        signOut={() => {}}
        role="admin"
        uid="admin-a"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Account menu' }));
    fireEvent.click(await screen.findByRole('tab', { name: 'API token' }));
    fireEvent.click(
      await screen.findByRole('button', { name: 'Create token' }),
    );
    expect(await screen.findByLabelText('New API token')).toHaveValue(
      placeholder,
    );
    fireEvent.click(screen.getByRole('tab', { name: 'Usage' }));
    fireEvent.click(screen.getByRole('tab', { name: 'API token' }));
    expect(screen.queryByDisplayValue(placeholder)).not.toBeInTheDocument();
  });
});
