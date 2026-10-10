import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AccountMenu } from '../../src/app/AuthenticatedApp';

// Settings metadata only: no credentials, tokens or posting content.
const config = {
  access_mode: 'openai_api_key',
  model_id: null,
  reasoning_effort: null,
  parallel_streams: 1,
  postings_per_run: 500,
  max_turns: 6,
  max_tool_calls: 12,
  max_output_tokens: 4096,
  request_timeout_seconds: 120,
  request_limit_per_run: 1000,
  custom_base_url: null,
  custom_context_window: null,
  custom_reasoning_field: null,
  custom_disable_with: null,
};
const missing = { configured: false, updated_at: null, expires_at: null };

function view(overrides: Record<string, unknown> = {}) {
  return {
    draft: config,
    active: null,
    activated_at: null,
    active_label: null,
    credentials: {
      openai_api_key: missing,
      chatgpt_signin: missing,
      codex_device: missing,
      custom_token: missing,
    },
    ...overrides,
  };
}

type Call = { path: string; method: string; body: unknown };

function fakeServer(
  routes: Record<string, (body: unknown) => unknown>,
): Call[] {
  const calls: Call[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = String(input);
    const method = init?.method ?? 'GET';
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path, method, body });
    const handler = routes[`${method} ${path}`];
    if (!handler) return new Response('{}', { status: 404 });
    return new Response(JSON.stringify(handler(body)), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  return calls;
}

async function openAiTab() {
  render(
    <AccountMenu
      profile={null}
      signOut={() => {}}
      role="admin"
      uid="admin-a"
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Account menu' }));
  fireEvent.click(await screen.findByRole('tab', { name: 'AI' }));
  await screen.findByText('Extraction not activated');
}

afterEach(() => vi.restoreAllMocks());

describe('AI settings tab', () => {
  it('saves a draft without activating and confirms activation explicitly', async () => {
    let current = view();
    const calls = fakeServer({
      'GET /api/admin/ai': () => current,
      'GET /api/admin/ai/login': () => ({ login: null }),
      'PUT /api/admin/ai': (body) => {
        current = view({ draft: body });
        return current;
      },
      'POST /api/admin/ai/activate': () => {
        current = view({
          draft: current.draft,
          active: current.draft,
          activated_at: '2026-10-10T00:00:00+00:00',
          active_label: 'Custom server / ctx384k',
        });
        return current;
      },
      'POST /api/admin/ai/custom/models': () => ({
        models: [
          {
            id: 'ctx384k',
            name: 'Qwen Flash',
            loaded: true,
            context_window: 393216,
            supports_tools: true,
            efforts: ['off', 'low', 'medium', 'xhigh'],
            default_effort: 'medium',
            reasoning_field: 'reasoning_effort',
            disable_with: { reasoning_effort: 'none' },
          },
        ],
      }),
    });
    await openAiTab();
    fireEvent.click(screen.getByRole('radio', { name: 'Custom server' }));
    fireEvent.change(screen.getByLabelText('Base URL'), {
      target: { value: 'http://100.64.0.10:8000/v1' },
    });
    const activate = screen.getByRole('button', {
      name: 'Use for extraction…',
    });
    expect(activate).toBeDisabled(); // unsaved drafts can never activate
    fireEvent.click(screen.getByRole('button', { name: 'Save AI settings' }));
    await screen.findByText(/Extraction is unchanged until you activate/);
    const saved = calls.find((call) => call.method === 'PUT');
    expect(saved?.body).toMatchObject({
      access_mode: 'custom',
      custom_base_url: 'http://100.64.0.10:8000/v1',
    });
    expect(calls.some((call) => call.path.endsWith('/activate'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Load models' }));
    await screen.findByText('Models loaded.');
    expect(
      calls.some((call) => call.path === '/api/admin/ai/custom/models'),
    ).toBe(true);
  });

  it('activates only after the AlertDialog confirmation', async () => {
    const ready = {
      ...config,
      access_mode: 'custom',
      model_id: 'ctx384k',
      custom_base_url: 'http://100.64.0.10:8000/v1',
      custom_context_window: 393216,
    };
    const calls = fakeServer({
      'GET /api/admin/ai': () => view({ draft: ready }),
      'GET /api/admin/ai/login': () => ({ login: null }),
      'POST /api/admin/ai/activate': () =>
        view({
          draft: ready,
          active: ready,
          activated_at: '2026-10-10T00:00:00+00:00',
          active_label: 'Custom server / ctx384k',
        }),
    });
    await openAiTab();
    fireEvent.click(
      screen.getByRole('button', { name: 'Use for extraction…' }),
    );
    expect(
      await screen.findByRole('alertdialog', {
        name: 'Use these AI settings?',
      }),
    ).toBeInTheDocument();
    expect(calls.some((call) => call.path.endsWith('/activate'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Activate' }));
    await screen.findByText('Active: Custom server / ctx384k');
    expect(calls.find((call) => call.path.endsWith('/activate'))?.body).toEqual(
      { confirm: true },
    );
  });

  it('runs the ChatGPT paste flow and clears the pasted address', async () => {
    const authorize = 'https://auth.openai.com/api/accounts/authorize?state=s';
    const calls = fakeServer({
      'GET /api/admin/ai': () =>
        view({ draft: { ...config, access_mode: 'chatgpt_signin' } }),
      'GET /api/admin/ai/login': () => ({ login: null }),
      'POST /api/admin/ai/chatgpt/start': () => ({
        login: {
          kind: 'chatgpt_signin',
          state: 'pending',
          expires_at: Math.floor(Date.now() / 1000) + 900,
          authorize_url: authorize,
        },
      }),
      'POST /api/admin/ai/chatgpt/complete': () => ({
        login: {
          kind: 'chatgpt_signin',
          state: 'complete',
          expires_at: Math.floor(Date.now() / 1000) + 900,
        },
      }),
    });
    await openAiTab();
    expect(
      screen.getByText(/remotely hosted apps are waitlisted/),
    ).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Connect ChatGPT' }));
    const link = await screen.findByRole('link', {
      name: /Open ChatGPT sign-in/,
    });
    expect(link).toHaveAttribute('href', authorize);
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    const pasted = 'http://127.0.0.1:1455/auth/callback?code=c&state=s';
    fireEvent.change(screen.getByLabelText('Pasted callback address'), {
      target: { value: pasted },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Finish sign-in' }));
    await screen.findByText('ChatGPT connected.');
    expect(
      calls.find((call) => call.path.endsWith('/chatgpt/complete'))?.body,
    ).toEqual({ callback_url: pasted });
    expect(screen.queryByLabelText('Pasted callback address')).toBeNull();
  });

  it('shows the Codex device code, marks it unofficial and can cancel', async () => {
    const calls = fakeServer({
      'GET /api/admin/ai': () =>
        view({ draft: { ...config, access_mode: 'codex_device' } }),
      'GET /api/admin/ai/login': () => ({ login: null }),
      'POST /api/admin/ai/codex/start': () => ({
        login: {
          kind: 'codex_device',
          state: 'pending',
          expires_at: Math.floor(Date.now() / 1000) + 900,
          user_code: 'ABCD-1234',
          verification_uri: 'https://auth.openai.com/codex/device',
          interval_seconds: 5,
        },
      }),
      'POST /api/admin/ai/login/cancel': () => ({
        login: {
          kind: 'codex_device',
          state: 'cancelled',
          expires_at: Math.floor(Date.now() / 1000) + 900,
        },
      }),
    });
    await openAiTab();
    expect(screen.getByText('Unofficial; may break')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Connect Codex' }));
    expect(await screen.findByLabelText('Device code')).toHaveValue(
      'ABCD-1234',
    );
    expect(
      screen.getByRole('link', { name: /Open device sign-in/ }),
    ).toHaveAttribute('href', 'https://auth.openai.com/codex/device');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await screen.findByText(/Last device login: cancelled/);
    await waitFor(() =>
      expect(calls.some((call) => call.path.endsWith('/login/cancel'))).toBe(
        true,
      ),
    );
  });
});
