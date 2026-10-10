import { readFileSync } from 'node:fs';
import { useState } from 'react';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AccountMenu } from '../../src/app/AuthenticatedApp';
import * as openaiSettings from '../../src/lib/useOpenAISettings';
import * as refreshStatus from '../../src/lib/useAdminRefreshStatus';

const emptyKey = {
  configured: false,
  status: 'not_configured',
  updated_at: null,
};

function openSettings() {
  render(
    <AccountMenu
      profile={null}
      signOut={() => {}}
      role="admin"
      uid="admin-a"
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Account menu' }));
}

afterEach(() => vi.restoreAllMocks());

describe('AdminSettings documented tab composition', () => {
  it('uses a bounded native dialog with fixed chrome and spaced form groups', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise(() => {}),
    );
    openSettings();
    const dialog = await screen.findByRole('dialog');
    expect(dialog.tagName).toBe('DIALOG');
    expect(dialog).toHaveAttribute('open');
    expect(dialog).toHaveAccessibleName('Admin settings');
    expect(dialog).toHaveClass('admin-settings-dialog');
    expect(screen.getByLabelText('WaterlooWorks password')).toHaveAttribute(
      'type',
      'password',
    );
    expect(dialog.querySelector('header')).toContainElement(
      screen.getByRole('button', { name: 'Close Admin settings' }),
    );
    expect(dialog.querySelector('footer')).toContainElement(
      screen.getByRole('button', { name: 'Done' }),
    );
    const css = readFileSync('src/app/AdminSettingsDialog.css', 'utf8');
    expect(css).toContain('width: min(960px, 90vw)');
    expect(css).toContain('height: min(760px, 80dvh)');
    expect(css).toContain('overflow: auto');
    for (const name of ['Usage', 'Refresh', 'Credentials']) {
      fireEvent.click(screen.getByRole('tab', { name }));
      expect(screen.getByRole('dialog')).toBe(dialog);
    }
    for (const name of ['WaterlooWorks credentials', 'OpenAI key']) {
      const form = screen.getByRole('form', { name });
      expect(form.firstElementChild).toHaveClass('grid', 'gap-4');
    }
  });

  it('starts with credentials, hides secondary content, and supports keyboard tab selection', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise(() => {}),
    );
    openSettings();
    const credentials = await screen.findByRole('tab', { name: 'Credentials' });
    expect(credentials).toHaveAttribute('aria-selected', 'true');
    expect(
      screen.getByRole('form', { name: 'WaterlooWorks credentials' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Save credentials' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: 'Save OpenAI key' }),
    ).toBeDisabled();
    expect(
      screen.queryByText('OpenAI usage and limits'),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('Current Duo approval')).not.toBeInTheDocument();
    expect(screen.queryByText(/Production Docker/)).not.toBeInTheDocument();
    credentials.focus();
    fireEvent.keyDown(credentials, { key: 'ArrowRight' });
    fireEvent.keyUp(credentials, { key: 'ArrowRight' });
    const usage = screen.getByRole('tab', { name: 'Usage' });
    await waitFor(() => expect(usage).toHaveFocus());
    expect(usage).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel', { name: 'Usage' })).toBeInTheDocument();
    expect(
      screen.queryByLabelText('WaterlooWorks password'),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Unknown is not zero/)).toBeInTheDocument();
    expect(screen.getByText(/not a guaranteed cap/)).toBeInTheDocument();
    expect(
      screen.queryByText(/20 attempts|\$1 known-cost/),
    ).not.toBeInTheDocument();
    // Styled semantic headings and a loading skeleton, not a merged
    // "unavailable or loading" message.
    expect(
      screen.getByRole('heading', {
        name: 'OpenAI usage and limits',
        level: 3,
      }),
    ).toHaveClass('typography');
    expect(
      screen.getByRole('button', { name: 'About these estimates' }),
    ).toHaveAttribute('aria-expanded', 'false');
    const loadingUsage = screen.getByRole('status', { name: 'Loading usage' });
    expect(loadingUsage).toHaveAttribute('aria-busy', 'true');
    expect(loadingUsage.querySelector('.skeleton')).not.toBeNull();
    expect(screen.queryByText(/Usage unavailable/)).not.toBeInTheDocument();
    fireEvent.keyDown(usage, { key: 'ArrowRight' });
    fireEvent.keyUp(usage, { key: 'ArrowRight' });
    expect(
      await screen.findByRole('tabpanel', { name: 'Refresh' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Start an explicit Refresh/)).toBeInTheDocument();
    expect(
      screen.queryByLabelText('Current Duo verification code'),
    ).not.toBeInTheDocument();
  });

  it('keeps unavailable states intelligible without leaking transport diagnostics', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(
      new Error(crypto.randomUUID()),
    );
    openSettings();
    await screen.findByText(/Credential settings unavailable/);
    expect(
      await screen.findByText(/OpenAI settings unavailable/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Refresh' }));
    expect(
      await screen.findByText(/Duo status unavailable/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Usage' }));
    const alert = (await screen.findByText('Usage unavailable')).closest(
      '.alert',
    ) as HTMLElement;
    expect(alert).toHaveClass('alert--warning');
    expect(alert).toHaveTextContent(/Unknown is not zero/);
    expect(
      screen.queryByRole('status', { name: 'Loading usage' }),
    ).not.toBeInTheDocument();
  });

  it.each(['Usage', 'Go to Refresh'])(
    'clears key drafts through %s, preserves WW drafts, and rereads only key metadata on return',
    async (destination) => {
      const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation((path) => {
        if (String(path).endsWith('waterlooworks-credentials'))
          return Promise.resolve(
            Response.json({ ...emptyKey, username: null }),
          );
        if (String(path).endsWith('openai-key'))
          return Promise.resolve(Response.json(emptyKey));
        if (String(path).endsWith('refresh-auth'))
          return Promise.resolve(
            Response.json({
              run_id: crypto.randomUUID(),
              approval_required: true,
              code: null,
              expires_in_seconds: 180,
            }),
          );
        return new Promise(() => {});
      });
      openSettings();
      await screen.findByText('Credentials not configured.');
      await screen.findByText('OpenAI key not configured.');
      const username = crypto.randomUUID();
      const password = crypto.randomUUID();
      fireEvent.change(screen.getByLabelText('WaterlooWorks username'), {
        target: { value: username },
      });
      fireEvent.change(screen.getByLabelText('WaterlooWorks password'), {
        target: { value: password },
      });
      fireEvent.change(screen.getByLabelText('Replacement OpenAI API key'), {
        target: { value: crypto.randomUUID() },
      });
      const reads = fetch.mock.calls.length;
      fireEvent.click(
        await screen.findByRole(destination === 'Usage' ? 'tab' : 'button', {
          name: destination,
        }),
      );
      expect(
        screen.queryByLabelText('Replacement OpenAI API key'),
      ).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('tab', { name: 'Credentials' }));
      await screen.findByText('OpenAI key not configured.');
      expect(screen.getByLabelText('Replacement OpenAI API key')).toHaveValue(
        '',
      );
      expect(
        (screen.getByLabelText('WaterlooWorks username') as HTMLInputElement)
          .value === username,
      ).toBe(true);
      expect(
        (screen.getByLabelText('WaterlooWorks password') as HTMLInputElement)
          .value === password,
      ).toBe(true);
      expect(fetch.mock.calls.length).toBe(reads + 1);
      expect(
        fetch.mock.calls.every(([, options]) => options?.method === 'GET'),
      ).toBe(true);
      fireEvent.click(screen.getByRole('button', { name: 'Done' }));
      // Unsaved WaterlooWorks drafts require an explicit discard.
      const confirm = await screen.findByRole('alertdialog', {
        name: 'Discard unsaved changes?',
      });
      expect(
        screen.getByRole('dialog', { name: 'Admin settings' }),
      ).toContainElement(confirm);
      fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
      await waitFor(() =>
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument(),
      );
      fireEvent.click(screen.getByRole('button', { name: 'Account menu' }));
      expect(
        await screen.findByRole('tab', { name: 'Credentials' }),
      ).toHaveAttribute('aria-selected', 'true');
      expect(screen.getByLabelText('WaterlooWorks password')).toHaveValue('');
    },
  );

  it.each(['WaterlooWorks username', 'Replacement OpenAI API key'])(
    'keeps a typed %s draft after an accidental Escape until discard is confirmed',
    async (label) => {
      vi.spyOn(globalThis, 'fetch').mockImplementation(
        () => new Promise(() => {}),
      );
      openSettings();
      const dialog = (await screen.findByRole('dialog')) as HTMLDialogElement;
      const value = crypto.randomUUID();
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
      fireEvent(dialog, new Event('cancel', { cancelable: true }));
      await screen.findByRole('alertdialog', {
        name: 'Discard unsaved changes?',
      });
      fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
      await waitFor(() =>
        expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument(),
      );
      expect(
        (screen.getByLabelText(label) as HTMLInputElement).value === value,
      ).toBe(true);
      expect(dialog.open).toBe(true);
    },
  );

  it('closes without confirmation when no draft was typed', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise(() => {}),
    );
    openSettings();
    const dialog = await screen.findByRole('dialog');
    fireEvent(dialog, new Event('cancel', { cancelable: true }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it.each(['Usage', 'Go to Refresh'])(
    'aborts a pending key read through %s and fences its late response before reconciling on return',
    async (destination) => {
      const pending: Array<{
        signal: AbortSignal;
        resolve: (response: Response) => void;
      }> = [];
      vi.spyOn(globalThis, 'fetch').mockImplementation((path, options) => {
        if (String(path).endsWith('openai-key'))
          return new Promise((resolve) =>
            pending.push({ signal: options!.signal!, resolve }),
          );
        if (String(path).endsWith('refresh-auth'))
          return Promise.resolve(
            Response.json({
              run_id: crypto.randomUUID(),
              approval_required: true,
              code: null,
              expires_in_seconds: 180,
            }),
          );
        return new Promise(() => {});
      });
      openSettings();
      await waitFor(() => expect(pending).toHaveLength(1));
      fireEvent.click(
        await screen.findByRole(destination === 'Usage' ? 'tab' : 'button', {
          name: destination,
        }),
      );
      expect(pending[0].signal.aborted).toBe(true);
      await act(async () => pending[0].resolve(Response.json(emptyKey)));
      fireEvent.click(screen.getByRole('tab', { name: 'Credentials' }));
      await waitFor(() => expect(pending).toHaveLength(2));
      expect(
        screen.queryByText('OpenAI key not configured.'),
      ).not.toBeInTheDocument();
      await act(async () => pending[1].resolve(Response.json(emptyKey)));
      expect(
        await screen.findByText('OpenAI key not configured.'),
      ).toBeInTheDocument();
    },
  );
});

describe('AdminSettings usage and pending-save boundaries', () => {
  it.each(['2E-8', '0', null])(
    'discloses exact %s cost and token primitives without treating unknown as zero',
    async (total) => {
      const runId = crypto.randomUUID();
      // Accounting primitives only, not an OpenAI completion or posting fixture.
      vi.spyOn(globalThis, 'fetch').mockImplementation((path) => {
        if (!String(path).endsWith('openai-usage'))
          return new Promise(() => {});
        return Promise.resolve(
          Response.json({
            default_request_limit: 7,
            default_known_cost_stop_usd: '0.125',
            pricing_version: '',
            warning: '',
            runs: [
              {
                run_id: runId,
                request_limit: 3,
                known_cost_stop_usd: '0.1',
                attempts: 0,
                unpriced_attempts: 0,
                known_usd: total ?? '0',
                total_usd: total,
                input_tokens: null,
                cached_tokens: 0,
                cache_write_tokens: null,
                output_tokens: 0,
                reasoning_tokens: null,
              },
            ],
          }),
        );
      });
      openSettings();
      fireEvent.click(await screen.findByRole('tab', { name: 'Usage' }));
      expect(
        await screen.findByText(
          '7 attempts per run · $0.125 USD known-cost stop',
        ),
      ).toBeInTheDocument();
      const summary = screen.getByRole('button', {
        name: new RegExp(`Run ${runId.slice(0, 8)}`),
      });
      expect(summary).toHaveAttribute('aria-expanded', 'false');
      expect(summary).toHaveTextContent(
        `0/3 attempts · $${total ?? '0'} USD known`,
      );
      expect(summary).toHaveTextContent(
        total === null ? 'Cost incomplete' : 'Known estimate',
      );
      fireEvent.click(summary);
      expect(summary).toHaveAttribute('aria-expanded', 'true');
      expect(await screen.findByText(`Run ID: ${runId}`)).toBeVisible();
      expect(screen.getByText(/Total USD:/)).toHaveTextContent(
        `Total USD: ${total === null ? 'unknown' : `$${total}`}; known-cost stop: $0.1; unpriced attempts: 0.`,
      );
      expect(screen.getByText(/Input: unknown/)).toHaveTextContent(
        'Input: unknown (includes cached read: 0, cache write: unknown). Output: 0 (includes reasoning: unknown).',
      );
      expect(screen.queryByText(/\$0\.00/)).not.toBeInTheDocument();
      expect(screen.getByText(/not a guaranteed cap/)).toBeVisible();
      // Shared disclaimers appear once, not repeated in every run panel.
      fireEvent.click(
        screen.getByRole('button', { name: 'About these estimates' }),
      );
      expect(await screen.findByText(/not extra tokens/)).toBeVisible();
      expect(screen.getAllByText(/not extra tokens/)).toHaveLength(1);
      expect(screen.getByText(/Partial or failed runs/)).toBeVisible();
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    },
  );

  it.each([
    [{ daily_cost_stop_usd: null, daily_cost_stop_invalid: true }, 'invalid'],
    [
      {
        daily_cost_stop_usd: '2.5',
        daily_cost_stop_invalid: false,
        rolling_24h_attempts: 3,
        rolling_24h_unpriced_attempts: 1,
        rolling_24h_known_usd: '0.01',
        rolling_24h_total_usd: null,
      },
      'set',
    ],
  ] as const)(
    'shows the rolling daily cost stop (%#: %s)',
    async (daily, kind) => {
      vi.spyOn(globalThis, 'fetch').mockImplementation((path) =>
        String(path).endsWith('openai-usage')
          ? Promise.resolve(
              Response.json({
                default_request_limit: 1,
                default_known_cost_stop_usd: '1',
                runs: [],
                ...daily,
              }),
            )
          : new Promise(() => {}),
      );
      openSettings();
      fireEvent.click(await screen.findByRole('tab', { name: 'Usage' }));
      await screen.findByText('No recorded OpenAI runs.');
      if (kind === 'invalid') {
        const alert = screen
          .getByText('Daily cost stop misconfigured')
          .closest('.alert') as HTMLElement;
        expect(alert).toHaveClass('alert--warning');
        expect(alert).toHaveTextContent(/WW_OPENAI_DAILY_COST_STOP_USD/);
      } else {
        expect(
          screen.getByText(
            '$2.5 USD rolling 24-hour known-cost stop · $0.01 USD known in the last 24 hours (3 attempts, 1 unpriced)',
          ),
        ).toBeInTheDocument();
        expect(
          screen.queryByText('Daily cost stop misconfigured'),
        ).not.toBeInTheDocument();
      }
    },
  );

  it('distinguishes an empty usage ledger from unavailable usage', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((path) =>
      String(path).endsWith('openai-usage')
        ? Promise.resolve(
            Response.json({
              default_request_limit: 0,
              default_known_cost_stop_usd: '0',
              runs: [],
            }),
          )
        : new Promise(() => {}),
    );
    openSettings();
    fireEvent.click(await screen.findByRole('tab', { name: 'Usage' }));
    expect(
      await screen.findByText('No recorded OpenAI runs.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Usage unavailable/)).not.toBeInTheDocument();
    expect(
      screen.getByText('0 attempts per run · $0 USD known-cost stop'),
    ).toBeInTheDocument();
  });

  it('fences a pending save on tab exit without claiming rollback and reconciles saved metadata on entry', async () => {
    let finish!: (response: Response) => void;
    let signal!: AbortSignal;
    let reads = 0;
    const configured = {
      configured: true,
      status: 'configured',
      updated_at: new Date().toISOString(),
    };
    vi.spyOn(globalThis, 'fetch').mockImplementation((path, options) => {
      if (!String(path).endsWith('openai-key')) return new Promise(() => {});
      if (options?.method === 'POST')
        return new Promise((resolve) => {
          finish = resolve;
          signal = options.signal!;
        });
      return Promise.resolve(
        Response.json(reads++ === 0 ? emptyKey : configured),
      );
    });
    openSettings();
    await screen.findByText('OpenAI key not configured.');
    fireEvent.change(screen.getByLabelText('Replacement OpenAI API key'), {
      target: { value: crypto.randomUUID() },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save OpenAI key' }));
    await screen.findByText('Saving OpenAI key…');
    await waitFor(() => expect(signal).toBeDefined());
    fireEvent.click(screen.getByRole('tab', { name: 'Usage' }));
    expect(signal.aborted).toBe(true);
    await act(async () => finish(Response.json(configured)));
    fireEvent.click(screen.getByRole('tab', { name: 'Credentials' }));
    expect(
      await screen.findByText('OpenAI key configured.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Saved\. OpenAI/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('Replacement OpenAI API key')).toHaveValue('');
    expect(reads).toBe(2);
  });

  it('announces approval arriving during a draft without switching tabs or focus', async () => {
    let finish!: (response: Response) => void;
    vi.spyOn(globalThis, 'fetch').mockImplementation((path) => {
      if (String(path).endsWith('waterlooworks-credentials'))
        return Promise.resolve(Response.json({ ...emptyKey, username: null }));
      if (String(path).endsWith('refresh-auth'))
        return new Promise((resolve) => {
          finish = resolve;
        });
      return new Promise(() => {});
    });
    openSettings();
    await screen.findByText('Credentials not configured.');
    const password = screen.getByLabelText('WaterlooWorks password');
    const draft = crypto.randomUUID();
    fireEvent.change(password, { target: { value: draft } });
    password.focus();
    // Random challenge metadata only; no Duo DOM or authentication simulation.
    await act(async () =>
      finish(
        Response.json({
          run_id: crypto.randomUUID(),
          approval_required: true,
          code: null,
          expires_in_seconds: 180,
        }),
      ),
    );
    expect(
      await screen.findByText('Duo approval required'),
    ).toBeInTheDocument();
    expect(password).toHaveFocus();
    expect((password as HTMLInputElement).value === draft).toBe(true);
    expect(screen.getByRole('tab', { name: 'Credentials' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Go to Refresh' }));
    expect(
      screen.getByText(/Approve only the refresh you requested/),
    ).toBeInTheDocument();
    expect(
      screen.queryByLabelText('Current Duo verification code'),
    ).not.toBeInTheDocument();
  });
});

// Unit presentation primitives only; this is not a simulated Google step-up.
describe('AdminSettings transient key presentation', () => {
  it.each([
    ['revealed', 'Usage'],
    ['verification-ready', 'Usage'],
    ['revealed', 'Go to Refresh'],
    ['verification-ready', 'Go to Refresh'],
  ] as const)(
    'hides %s state when leaving Credentials through %s',
    async (state, destination) => {
      const hide = vi.fn();
      vi.spyOn(globalThis, 'fetch').mockImplementation(
        () => new Promise(() => {}),
      );
      vi.spyOn(refreshStatus, 'useAdminRefreshStatus').mockReturnValue({
        challenge: {
          run_id: crypto.randomUUID(),
          approval_required: true,
          code: null,
          expires_in_seconds: 180,
        },
        usage: null,
        usageError: false,
        pacing: null,
        message: '',
        clear: vi.fn(),
      });
      vi.spyOn(openaiSettings, 'useOpenAISettings').mockImplementation(
        function useTransientKey() {
          const [revealed, setRevealed] = useState(
            state === 'revealed' ? crypto.randomUUID() : '',
          );
          const [readyToVerify, setReadyToVerify] = useState(
            state === 'verification-ready',
          );
          return {
            draft: '',
            setDraft: vi.fn(),
            revealed,
            configured: true,
            pending: false,
            message: '',
            readyToVerify,
            save: vi.fn(),
            prepareReveal: vi.fn(),
            verify: vi.fn(),
            hide: () => {
              hide();
              setRevealed('');
              setReadyToVerify(false);
            },
          };
        },
      );
      openSettings();
      if (state === 'revealed')
        expect(
          await screen.findByLabelText('Revealed OpenAI API key'),
        ).toBeInTheDocument();
      else
        expect(
          await screen.findByRole('button', { name: 'Verify with Google' }),
        ).toBeInTheDocument();
      fireEvent.click(
        screen.getByRole(destination === 'Usage' ? 'tab' : 'button', {
          name: destination,
        }),
      );
      expect(hide).toHaveBeenCalledOnce();
      expect(
        screen.queryByLabelText('Revealed OpenAI API key'),
      ).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('tab', { name: 'Credentials' }));
      expect(
        screen.queryByLabelText('Revealed OpenAI API key'),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole('button', { name: 'Verify with Google' }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole('button', { name: 'Reveal' }),
      ).toBeInTheDocument();
    },
  );
});
