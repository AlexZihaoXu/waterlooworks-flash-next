import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AccountMenu } from '../../src/app/AuthenticatedApp';
import {
  parseApprovalListing,
  parseApprovalTree,
  refusalMessage,
} from '../../src/lib/useApprovals';

// Structural identity rows only: placeholder names, invalid-TLD addresses
// and the fixed Google avatar host; nothing is a real account.
const at = '2026-01-01T00:00:00+00:00';
const later = '2026-01-02T00:00:00+00:00';
const zac = {
  id: 'zac',
  display_name: 'Person Z',
  email: 'z@example.invalid',
  photo_url: 'https://lh3.googleusercontent.com/a/structural',
  requested_at: at,
};
const devin = {
  id: 'devin',
  display_name: null,
  email: 'd@example.invalid',
  photo_url: null,
  requested_at: later,
};
const approvedZac = {
  id: 'zac',
  display_name: 'Person Z',
  photo_url: null,
  approved_at: later,
};
const empty = { pending: [], approved_by_me: [] };
const tree = {
  root: {
    id: 'admin-a',
    display_name: 'Admin A',
    email: 'a@example.invalid',
    photo_url: null,
    role: 'admin',
    approved_at: at,
    approved_by: null,
    children: [
      {
        id: 'zac',
        display_name: 'Person Z',
        email: 'z@example.invalid',
        photo_url: null,
        role: 'member',
        approved_at: later,
        approved_by: 'Admin A',
        children: [
          {
            id: 'henry',
            display_name: 'Person H',
            email: null,
            photo_url: null,
            role: 'member',
            approved_at: later,
            approved_by: 'Person Z',
            children: [],
          },
        ],
      },
    ],
  },
  declined: [
    {
      id: 'gone',
      display_name: 'Person G',
      email: 'g@example.invalid',
      photo_url: null,
      status: 'declined',
      decided_at: later,
      decided_by: 'Admin A',
    },
  ],
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

async function openApprovals(role: 'admin' | 'member' = 'member') {
  render(
    <AccountMenu
      profile={null}
      signOut={() => {}}
      role={role}
      uid={role === 'admin' ? 'admin-a' : 'member-a'}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Account menu' }));
  if (role === 'member')
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Settings' }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(await screen.findByRole('tab', { name: /^Approvals/ }));
  return dialog;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('approval parsing', () => {
  it('accepts the documented shapes and drops anything unexpected', () => {
    expect(
      parseApprovalListing({ pending: [zac, devin], approved_by_me: [] }),
    ).toEqual({ pending: [zac, devin], approved_by_me: [] });
    expect(
      parseApprovalListing({
        pending: [],
        approved_by_me: [
          { ...approvedZac, photo_url: 'https://evil.invalid/x.png' },
        ],
      }).approved_by_me[0].photo_url,
    ).toBeNull();
    expect(() => parseApprovalListing({ pending: [] })).toThrow();
    expect(() =>
      parseApprovalListing({
        pending: [{ ...zac, extra: 1 }],
        approved_by_me: [],
      }),
    ).toThrow();
    expect(() =>
      parseApprovalListing({
        pending: [{ ...zac, id: 'a b' }],
        approved_by_me: [],
      }),
    ).toThrow();
    expect(parseApprovalTree(tree)).toEqual(tree);
    expect(parseApprovalTree({ root: null, declined: [] })).toEqual({
      root: null,
      declined: [],
    });
    expect(() =>
      parseApprovalTree({
        root: { ...tree.root, role: 'owner' },
        declined: [],
      }),
    ).toThrow();
    expect(() =>
      parseApprovalTree({
        root: null,
        declined: [{ ...tree.declined[0], status: 'banned' }],
      }),
    ).toThrow();
  });
  it('uses fixed wording for refusals, never server text', () => {
    expect(refusalMessage(new Error('x'), 'approval')).toBe(
      'The approval could not be confirmed. Reopen settings to check.',
    );
  });
});

describe('member settings: Approvals tab', () => {
  it('lists people waiting, shows the count on the account button and approves', async () => {
    let listing: object = { pending: [zac, devin], approved_by_me: [] };
    const calls = fakeServer({
      'GET /api/approvals': () => listing,
      'POST /api/approvals/zac/approve': () => {
        listing = { pending: [devin], approved_by_me: [approvedZac] };
        return listing;
      },
    });
    const dialog = await openApprovals();
    // The badge reads from the same listing loaded on mount.
    await waitFor(() =>
      expect(
        within(screen.getByRole('button', { name: 'Account menu' })).getByText(
          '2',
        ),
      ).toBeInTheDocument(),
    );
    const waiting = await within(dialog).findByRole('list', {
      name: 'People waiting for approval',
    });
    expect(within(waiting).getAllByRole('listitem')).toHaveLength(2);
    expect(within(waiting).getByText('Person Z')).toBeInTheDocument();
    expect(within(waiting).getByText('z@example.invalid')).toBeInTheDocument();
    // Without a name the verified address stands in, shown once.
    expect(within(waiting).getByText('d@example.invalid')).toBeInTheDocument();
    expect(within(waiting).getAllByText(/^Requested /)).toHaveLength(2);
    expect(
      within(dialog).getByText('You have not approved anyone yet.'),
    ).toBeInTheDocument();
    fireEvent.click(
      within(waiting).getByRole('button', { name: 'Approve Person Z' }),
    );
    expect(
      await within(dialog).findByText('Person Z can use the app now.'),
    ).toBeInTheDocument();
    expect(calls.find((call) => call.method === 'POST')?.body).toEqual({});
    expect(
      within(
        within(dialog).getByRole('list', { name: 'People you approved' }),
      ).getByText('Person Z'),
    ).toBeInTheDocument();
    expect(within(waiting).getAllByRole('listitem')).toHaveLength(1);
    // Members never see the administrator's tree.
    expect(
      within(dialog).queryByText('Everyone with access'),
    ).not.toBeInTheDocument();
    expect(JSON.stringify({ ...localStorage })).not.toContain(
      'example.invalid',
    );
  });

  it('confirms before declining and shows the empty state afterwards', async () => {
    let listing: object = { pending: [zac], approved_by_me: [] };
    const calls = fakeServer({
      'GET /api/approvals': () => listing,
      'POST /api/approvals/zac/decline': () => {
        listing = empty;
        return listing;
      },
    });
    const dialog = await openApprovals();
    fireEvent.click(
      await within(dialog).findByRole('button', { name: 'Decline Person Z' }),
    );
    const confirm = await screen.findByRole('alertdialog', {
      name: 'Decline Person Z?',
    });
    expect(confirm).toHaveTextContent(/not approved/);
    expect(calls.some((call) => call.method === 'POST')).toBe(false);
    fireEvent.click(within(confirm).getByRole('button', { name: 'Decline' }));
    expect(
      await within(dialog).findByText('Person Z was declined.'),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByText(/Nobody is waiting right now/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Account menu' }),
    ).not.toHaveTextContent('1');
  });

  it('explains a request someone else already decided and refreshes', async () => {
    // Someone else decides between this user's read and their click.
    let decided = false;
    fakeServer({
      'GET /api/approvals': () =>
        decided ? empty : { pending: [zac], approved_by_me: [] },
      'POST /api/approvals/zac/approve': () => {
        decided = true;
        return new Response(JSON.stringify({ detail: 'internal detail' }), {
          status: 409,
        });
      },
    });
    const dialog = await openApprovals();
    fireEvent.click(
      await within(dialog).findByRole('button', { name: 'Approve Person Z' }),
    );
    expect(
      await within(dialog).findByText(/already decided this request/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/internal detail/)).not.toBeInTheDocument();
    await waitFor(() =>
      expect(
        within(dialog).getByText(/Nobody is waiting right now/),
      ).toBeInTheDocument(),
    );
  });
});

describe('admin settings: Approvals tab', () => {
  it('shows the nested tree with who approved whom, removes with a confirmation and reinstates', async () => {
    let current: object = tree;
    const calls = fakeServer({
      'GET /api/approvals': () => empty,
      'GET /api/admin/approvals': () => current,
      'POST /api/admin/approvals/zac/remove': () => {
        current = {
          root: {
            ...tree.root,
            children: [{ ...tree.root.children[0].children[0] }],
          },
          declined: [
            {
              ...tree.declined[0],
              id: 'zac',
              display_name: 'Person Z',
              status: 'removed',
            },
            tree.declined[0],
          ],
        };
        return current;
      },
      'POST /api/admin/approvals/gone/reinstate': () => {
        current = { ...(current as typeof tree), declined: [] };
        return current;
      },
      // Other admin tabs are never read on this tab.
    });
    const dialog = await openApprovals('admin');
    const everyone = await within(dialog).findByRole('list', {
      name: 'Approval tree',
    });
    expect(within(everyone).getByText('Administrator')).toBeInTheDocument();
    const underAdmin = within(everyone).getByRole('list', {
      name: 'Approved by Admin A',
    });
    expect(within(underAdmin).getByText('Person Z')).toBeInTheDocument();
    expect(
      within(underAdmin).getByText(/^Approved by Admin A/),
    ).toBeInTheDocument();
    const underZac = within(underAdmin).getByRole('list', {
      name: 'Approved by Person Z',
    });
    expect(within(underZac).getByText('Person H')).toBeInTheDocument();
    expect(
      within(dialog).queryByRole('button', { name: /Remove Admin A/ }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      within(everyone).getByRole('button', { name: 'Remove Person Z' }),
    );
    const confirm = await screen.findByRole('alertdialog', {
      name: 'Remove Person Z?',
    });
    expect(confirm).toHaveTextContent(/moves up under their approver/);
    expect(calls.some((call) => call.method === 'POST')).toBe(false);
    fireEvent.click(
      within(confirm).getByRole('button', { name: 'Remove access' }),
    );
    expect(
      await within(dialog).findByText('Person Z no longer has access.'),
    ).toBeInTheDocument();
    // Henry moved up under the administrator.
    expect(
      within(
        within(dialog).getByRole('list', { name: 'Approved by Admin A' }),
      ).getByText('Person H'),
    ).toBeInTheDocument();
    const declined = within(dialog).getByRole('list', {
      name: 'Declined and removed people',
    });
    expect(within(declined).getByText('Removed')).toBeInTheDocument();
    expect(within(declined).getByText('Declined')).toBeInTheDocument();
    fireEvent.click(
      within(declined).getByRole('button', {
        name: 'Move Person G back to pending',
      }),
    );
    expect(
      await within(dialog).findByText(
        'Person G is waiting for approval again.',
      ),
    ).toBeInTheDocument();
    expect(
      calls.filter((call) => call.method === 'POST').map((call) => call.path),
    ).toEqual([
      '/api/admin/approvals/zac/remove',
      '/api/admin/approvals/gone/reinstate',
    ]);
    expect(
      calls.every(
        (call) =>
          call.path.startsWith('/api/approvals') ||
          call.path.startsWith('/api/admin/approvals') ||
          call.path.startsWith('/api/admin/waterlooworks-credentials') ||
          call.path.startsWith('/api/admin/openai-key') ||
          call.path.startsWith('/api/admin/refresh-auth') ||
          call.path.startsWith('/api/admin/openai-usage'),
      ),
    ).toBe(true);
  });
});
