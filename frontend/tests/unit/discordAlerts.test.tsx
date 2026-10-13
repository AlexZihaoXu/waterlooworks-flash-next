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
  isDiscordWebhookUrl,
  parseWebhook,
  parseWebhookList,
} from '../../src/lib/useDiscordWebhooks';

// Structurally valid placeholders only; never a real webhook.
const token = 'x'.repeat(68);
const url = `https://discord.com/api/webhooks/1234567890/${token}`;
const at = '2026-01-01T00:00:00+00:00';

function hook(overrides: object = {}) {
  return {
    id: 1,
    label: 'Jobs channel',
    hint: '…7890',
    enabled: true,
    created_at: at,
    last_sent_at: null,
    last_status: null,
    last_error_at: null,
    ...overrides,
  };
}

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
          status: method === 'POST' && path.endsWith('webhooks') ? 201 : 200,
          headers: { 'Content-Type': 'application/json' },
        });
  });
  return calls;
}

const endpoint = '/api/account/discord-webhooks';
const tokenRoute = {
  'GET /api/account/api-token': () => ({
    configured: false,
    hint: null,
    created_at: null,
    last_used_at: null,
  }),
};

async function openMemberDiscord() {
  render(<AccountMenu profile={null} signOut={() => {}} uid="member-a" />);
  fireEvent.click(screen.getByRole('button', { name: 'Account menu' }));
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Settings' }));
  const dialog = await screen.findByRole('dialog', { name: 'Settings' });
  fireEvent.click(within(dialog).getByRole('tab', { name: 'Discord alerts' }));
  return dialog;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('Discord webhook parsing', () => {
  it('accepts only documented metadata and never a URL field', () => {
    expect(parseWebhook(hook())).toEqual(hook());
    expect(() => parseWebhook({ ...hook(), url })).toThrow();
    expect(() => parseWebhook({ ...hook(), last_status: 'maybe' })).toThrow();
    expect(() => parseWebhook({ ...hook(), id: '1' })).toThrow();
    expect(
      parseWebhookList({ webhooks: [hook()], limit: 10, available: true }),
    ).toEqual({ webhooks: [hook()], limit: 10, available: true });
    expect(() => parseWebhookList({ webhooks: [], limit: 10 })).toThrow();
  });

  it('recognises Discord webhook URLs like the server does', () => {
    expect(isDiscordWebhookUrl(url)).toBe(true);
    expect(isDiscordWebhookUrl(` ${url} `)).toBe(true);
    for (const host of [
      'discordapp.com',
      'ptb.discord.com',
      'canary.discord.com',
    ])
      expect(isDiscordWebhookUrl(url.replace('discord.com', host))).toBe(true);
    for (const bad of [
      url.replace('https', 'http'),
      url.replace('discord.com', 'example.com'),
      `${url}?wait=true`,
      `${url}/`,
      url.replace('1234567890', 'abc'),
      'not a url',
    ])
      expect(isDiscordWebhookUrl(bad)).toBe(false);
  });
});

describe('Discord alerts settings', () => {
  it('members add a webhook without the URL being shown afterwards', async () => {
    let webhooks: object[] = [];
    const calls = fakeServer({
      ...tokenRoute,
      [`GET ${endpoint}`]: () => ({ webhooks, limit: 10, available: true }),
      [`POST ${endpoint}`]: () => {
        webhooks = [hook()];
        return hook();
      },
    });
    await openMemberDiscord();
    expect(
      await screen.findByText(/No webhooks yet/, undefined, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Server Settings → Integrations → Webhooks/),
    ).toBeInTheDocument();
    const add = screen.getByRole('button', { name: 'Add webhook' });
    expect(add).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Name'), {
      target: { value: 'Jobs channel' },
    });
    const field = screen.getByLabelText('Webhook URL') as HTMLInputElement;
    expect(field.type).toBe('password');
    fireEvent.change(field, { target: { value: 'https://example.com/x' } });
    expect(add).toBeDisabled();
    expect(screen.getByText(/isn.t a Discord webhook URL/)).toBeInTheDocument();
    fireEvent.change(field, { target: { value: url } });
    expect(add).toBeEnabled();
    fireEvent.click(add);
    expect(await screen.findByText('Jobs channel')).toBeInTheDocument();
    expect(calls.find((call) => call.method === 'POST')?.body).toEqual({
      label: 'Jobs channel',
      url,
    });
    expect(field).toHaveValue('');
    expect(document.body.innerHTML).not.toContain(token);
    expect(screen.getByText('…7890')).toBeInTheDocument();
    expect(screen.getByText('Not sent yet')).toBeInTheDocument();
  });

  it('toggles, tests and deletes a webhook after confirmation', async () => {
    let current: Record<string, unknown> = hook({
      last_status: 'ok',
      last_sent_at: at,
    });
    const calls = fakeServer({
      ...tokenRoute,
      [`GET ${endpoint}`]: () => ({
        webhooks: [current],
        limit: 10,
        available: true,
      }),
      [`PATCH ${endpoint}/1`]: (body) => {
        current = { ...current, ...(body as object) };
        return current;
      },
      [`POST ${endpoint}/1/test`]: () => {
        current = { ...current, last_status: 'gone', enabled: false };
        return current;
      },
      [`DELETE ${endpoint}/1`]: () => ({
        webhooks: [],
        limit: 10,
        available: true,
      }),
    });
    await openMemberDiscord();
    expect(await screen.findByText(/^Last sent /)).toBeInTheDocument();
    const toggle = screen.getByRole('switch', {
      name: 'Alerts for Jobs channel',
    });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(calls.find((call) => call.method === 'PATCH')?.body).toEqual({
        enabled: false,
      }),
    );
    await waitFor(() => expect(toggle).not.toBeChecked());
    fireEvent.click(screen.getByRole('button', { name: 'Test Jobs channel' }));
    expect(
      await screen.findByText(/Discord says this webhook was deleted/),
    ).toBeInTheDocument();
    expect(screen.getByText('Deleted in Discord')).toBeInTheDocument();
    expect(calls.find((call) => call.path.endsWith('/test'))?.body).toEqual({});
    fireEvent.click(
      screen.getByRole('button', { name: 'Delete Jobs channel' }),
    );
    const confirm = await screen.findByRole('alertdialog', {
      name: 'Delete this webhook?',
    });
    fireEvent.click(
      within(confirm).getByRole('button', { name: 'Delete webhook' }),
    );
    expect(await screen.findByText(/No webhooks yet/)).toBeInTheDocument();
    expect(calls.some((call) => call.method === 'DELETE')).toBe(true);
  });

  it('explains refused additions without echoing the URL', async () => {
    fakeServer({
      ...tokenRoute,
      [`GET ${endpoint}`]: () => ({
        webhooks: [],
        limit: 10,
        available: true,
      }),
      [`POST ${endpoint}`]: () =>
        new Response(JSON.stringify({ detail: url }), { status: 409 }),
    });
    await openMemberDiscord();
    fireEvent.change(await screen.findByLabelText('Name'), {
      target: { value: 'Jobs' },
    });
    fireEvent.change(screen.getByLabelText('Webhook URL'), {
      target: { value: url },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add webhook' }));
    expect(await screen.findByText(/already in your list/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(token);
  });

  it('asks before closing settings with a typed, unsaved webhook URL', async () => {
    fakeServer({
      ...tokenRoute,
      [`GET ${endpoint}`]: () => ({
        webhooks: [],
        limit: 10,
        available: true,
      }),
    });
    await openMemberDiscord();
    fireEvent.change(await screen.findByLabelText('Webhook URL'), {
      target: { value: url },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    const confirm = await screen.findByRole('alertdialog', {
      name: 'Discard the webhook?',
    });
    fireEvent.click(within(confirm).getByRole('button', { name: 'Discard' }));
    await waitFor(() =>
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
    );
  });

  it('shows when the server cannot store webhooks', async () => {
    fakeServer({
      ...tokenRoute,
      [`GET ${endpoint}`]: () => ({
        webhooks: [],
        limit: 10,
        available: false,
      }),
    });
    await openMemberDiscord();
    expect(
      await screen.findByText('Discord alerts are unavailable'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add webhook' })).toBeDisabled();
  });

  it('admins get a Discord alerts tab', async () => {
    fakeServer({
      [`GET ${endpoint}`]: () => ({
        webhooks: [hook()],
        limit: 10,
        available: true,
      }),
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
    const dialog = await screen.findByRole('dialog', {
      name: 'Admin settings',
    });
    fireEvent.click(
      within(dialog).getByRole('tab', { name: 'Discord alerts' }),
    );
    expect(await screen.findByText('Jobs channel')).toBeInTheDocument();
  });
});
