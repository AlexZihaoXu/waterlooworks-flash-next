import { useEffect, useRef, useState } from 'react';
import {
  AuthGeneration,
  parseRetryAfter,
  runAuthOperation,
} from './authLifecycle';
import type { components } from './api.generated';

export type DiscordWebhook = components['schemas']['DiscordWebhook'];
export type DiscordWebhookList = components['schemas']['DiscordWebhookList'];

const endpoint = '/api/account/discord-webhooks';
export const LABEL_MAX = 60;
const STATUSES = ['ok', 'failed', 'gone'];
const HOSTS = [
  'discord.com',
  'discordapp.com',
  'ptb.discord.com',
  'canary.discord.com',
];
const WEBHOOK =
  /^https:\/\/([A-Za-z.]+)\/api\/webhooks\/\d{1,25}\/[A-Za-z0-9_-]{1,128}$/;

/** Same shape the server accepts; it still validates and normalises. */
export function isDiscordWebhookUrl(value: string): boolean {
  const trimmed = value.trim();
  const match = trimmed.length <= 300 ? WEBHOOK.exec(trimmed) : null;
  return match !== null && HOSTS.includes(match[1].toLowerCase());
}

const isTime = (value: unknown) =>
  typeof value === 'string' &&
  value.length <= 64 &&
  !Number.isNaN(Date.parse(value));
const optionalTime = (value: unknown) => value === null || isTime(value);

export function parseWebhook(value: unknown): DiscordWebhook {
  if (!value || typeof value !== 'object')
    throw new Error('Webhook unavailable.');
  const data = value as Record<string, unknown>;
  const allowed = [
    'id',
    'label',
    'hint',
    'enabled',
    'created_at',
    'last_sent_at',
    'last_status',
    'last_error_at',
  ];
  if (
    Object.keys(data).length !== allowed.length ||
    Object.keys(data).some((key) => !allowed.includes(key)) ||
    !Number.isSafeInteger(data.id) ||
    typeof data.label !== 'string' ||
    data.label.length > LABEL_MAX ||
    typeof data.hint !== 'string' ||
    data.hint.length > 16 ||
    typeof data.enabled !== 'boolean' ||
    !isTime(data.created_at) ||
    !optionalTime(data.last_sent_at) ||
    !optionalTime(data.last_error_at) ||
    !(
      data.last_status === null || STATUSES.includes(data.last_status as string)
    )
  )
    throw new Error('Webhook unavailable.');
  return data as DiscordWebhook;
}

export function parseWebhookList(value: unknown): DiscordWebhookList {
  if (!value || typeof value !== 'object')
    throw new Error('Webhooks unavailable.');
  const data = value as Record<string, unknown>;
  if (
    Object.keys(data).length !== 3 ||
    !Array.isArray(data.webhooks) ||
    !Number.isSafeInteger(data.limit) ||
    typeof data.available !== 'boolean'
  )
    throw new Error('Webhooks unavailable.');
  return {
    webhooks: data.webhooks.map(parseWebhook),
    limit: data.limit as number,
    available: data.available,
  };
}

class WebhookHttpError extends Error {
  readonly status: number;
  readonly retryAfter: number | null;
  constructor(status: number, retryAfter: number | null) {
    super('Webhook request refused.');
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

function request(
  path: string,
  signal: AbortSignal,
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE' = 'GET',
  body: object = {},
): Promise<unknown> {
  return runAuthOperation(async (operation) => {
    const response = await operation.wait(
      fetch(path, {
        method,
        body: method === 'GET' ? undefined : JSON.stringify(body),
        headers:
          method === 'GET'
            ? undefined
            : { 'Content-Type': 'application/json', 'X-WW-Request': '1' },
        credentials: 'same-origin',
        cache: 'no-store',
        signal: operation.signal,
      }),
    );
    // Server text is never shown: only the status and Retry-After are used.
    if (!response.ok)
      throw new WebhookHttpError(
        response.status,
        parseRetryAfter(response.headers.get('Retry-After')),
      );
    return operation.wait(response.json());
  }, signal);
}

const unavailableMessage =
  'Discord alerts are unavailable on this server right now. Ask the administrator.';

function testMessage(webhook: DiscordWebhook): string {
  if (webhook.last_status === 'ok')
    return `Test message sent to “${webhook.label}”. Check the channel in Discord.`;
  if (webhook.last_status === 'gone')
    return 'Discord says this webhook was deleted, so its alerts were turned off. Delete it and add a new one.';
  return 'Discord did not accept the test message. Check the webhook in Discord and try again.';
}

/**
 * The signed-in user's Discord alert webhooks while the section is shown.
 * The webhook URL draft lives only in this hook's memory; it is cleared after
 * a successful add and whenever the section closes. Saved URLs never return.
 */
export function useDiscordWebhooks(isOpen: boolean, uid: string) {
  const lifecycle = useRef(new AuthGeneration());
  const busy = useRef(false);
  const [list, setList] = useState<DiscordWebhookList | null>(null);
  const [label, setLabel] = useState('');
  const [url, setUrl] = useState('');
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');

  useEffect(() => {
    if (!isOpen) return;
    const lifetime = lifecycle.current;
    const generation = lifetime.begin();
    queueMicrotask(() => {
      if (!lifetime.current(generation)) return;
      busy.current = true;
      setPending(true);
      void request(endpoint, lifetime.signal)
        .then(parseWebhookList)
        .then((loaded) => {
          if (lifetime.current(generation)) setList(loaded);
        })
        .catch(() => {
          if (lifetime.current(generation))
            setMessage(
              'Discord webhooks unavailable. Reopen settings to retry.',
            );
        })
        .finally(() => {
          if (lifetime.current(generation)) {
            busy.current = false;
            setPending(false);
          }
        });
    });
    return () => {
      lifetime.begin();
      busy.current = false;
      setList(null);
      setLabel('');
      setUrl('');
      setPending(false);
      setMessage('');
    };
  }, [isOpen, uid]);

  const run = async (
    work: (signal: AbortSignal) => Promise<void>,
    refused: (error: WebhookHttpError) => string,
  ) => {
    if (!isOpen || busy.current) return;
    const lifetime = lifecycle.current;
    const generation = lifetime.begin();
    busy.current = true;
    setPending(true);
    setMessage('');
    try {
      await work(lifetime.signal);
    } catch (error) {
      if (!lifetime.current(generation)) return;
      setMessage(
        error instanceof WebhookHttpError
          ? error.status === 503
            ? unavailableMessage
            : refused(error)
          : 'The change could not be confirmed. Reopen settings to check your webhooks.',
      );
    } finally {
      if (lifetime.current(generation)) {
        busy.current = false;
        setPending(false);
      }
    }
  };

  const replace = (webhook: DiscordWebhook) =>
    setList((current) =>
      current
        ? {
            ...current,
            webhooks: current.webhooks.map((item) =>
              item.id === webhook.id ? webhook : item,
            ),
          }
        : current,
    );

  const add = () =>
    run(
      async (signal) => {
        const added = parseWebhook(
          await request(endpoint, signal, 'POST', {
            label: label.trim(),
            url: url.trim(),
          }),
        );
        if (signal.aborted) return;
        setList((current) =>
          current
            ? { ...current, webhooks: [...current.webhooks, added] }
            : current,
        );
        setUrl('');
        setLabel('');
        setMessage(`Added “${added.label}”. Send a test to check it.`);
      },
      (error) =>
        error.status === 409
          ? (list?.webhooks.length ?? 0) >= (list?.limit ?? 10)
            ? `You can add up to ${list?.limit ?? 10} webhooks. Delete one first.`
            : 'That webhook is already in your list.'
          : error.status === 422
            ? 'That isn’t a Discord webhook URL. Copy it again from Discord.'
            : 'The webhook could not be added. Try again.',
    );

  const setEnabled = (webhook: DiscordWebhook, enabled: boolean) =>
    run(
      async (signal) => {
        const updated = parseWebhook(
          await request(`${endpoint}/${webhook.id}`, signal, 'PATCH', {
            enabled,
          }),
        );
        if (!signal.aborted) replace(updated);
      },
      () => 'The webhook could not be updated. Reopen settings to check it.',
    );

  const test = (webhook: DiscordWebhook) =>
    run(
      async (signal) => {
        const updated = parseWebhook(
          await request(`${endpoint}/${webhook.id}/test`, signal, 'POST'),
        );
        if (signal.aborted) return;
        replace(updated);
        setMessage(testMessage(updated));
      },
      (error) =>
        error.status === 429
          ? 'Too many test messages. Try again in a minute.'
          : 'The test message could not be sent. Try again.',
    );

  const remove = (webhook: DiscordWebhook) =>
    run(
      async (signal) => {
        const remaining = parseWebhookList(
          await request(`${endpoint}/${webhook.id}`, signal, 'DELETE'),
        );
        if (signal.aborted) return;
        setList(remaining);
        setMessage(`Deleted “${webhook.label}”.`);
      },
      () => 'The webhook could not be deleted. Reopen settings to check it.',
    );

  return {
    list,
    label,
    setLabel,
    url,
    setUrl,
    pending,
    message,
    add,
    setEnabled,
    test,
    remove,
  };
}

export type DiscordWebhookSettings = ReturnType<typeof useDiscordWebhooks>;

/** Settings dismissal wording while a typed webhook URL is unsaved. */
export const unsavedWebhookDiscard = {
  heading: 'Discard the webhook?',
  body: 'The webhook you were adding has not been saved. Closing settings clears it.',
  confirm: 'Discard',
  cancel: 'Keep editing',
};
