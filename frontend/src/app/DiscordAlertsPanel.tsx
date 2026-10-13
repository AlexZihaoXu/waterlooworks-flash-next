import { useCallback, useState } from 'react';
import { LuPlus, LuSend, LuTrash2 } from 'react-icons/lu';
import {
  Accordion,
  Alert,
  AlertDialog,
  Button,
  Card,
  Chip,
  Description,
  FieldError,
  Form,
  Input,
  Label,
  Switch,
  TextField,
  Typography,
} from '@heroui/react';
import {
  LABEL_MAX,
  isDiscordWebhookUrl,
  type DiscordWebhook,
  type DiscordWebhookSettings,
} from '../lib/useDiscordWebhooks';
import { relativeTime, torontoDateTime } from '../lib/refreshSchedule';
import { useRelativeNow } from '../lib/useRelativeNow';

function when(label: string, value: string | null, now: number) {
  if (!value) return null;
  return `${label} ${relativeTime(Date.parse(value), now)} · ${torontoDateTime(value)}`;
}

function status(webhook: DiscordWebhook, now: number) {
  switch (webhook.last_status) {
    case 'ok':
      return {
        chip: (
          <Chip size="sm" variant="soft" color="success">
            Delivered
          </Chip>
        ),
        detail: when('Last sent', webhook.last_sent_at, now),
      };
    case 'failed':
      return {
        chip: (
          <Chip size="sm" variant="soft" color="warning">
            Last send failed
          </Chip>
        ),
        detail: when('Failed', webhook.last_error_at, now),
      };
    case 'gone':
      return {
        chip: (
          <Chip size="sm" variant="soft" color="danger">
            Deleted in Discord
          </Chip>
        ),
        detail: when('Turned off', webhook.last_error_at, now),
      };
    default:
      return {
        chip: (
          <Chip size="sm" variant="soft">
            Not sent yet
          </Chip>
        ),
        detail: null,
      };
  }
}

/**
 * Documented HeroUI Card/Form/TextField/Switch/AlertDialog composition for a
 * user's Discord new-job alert webhooks. Overlays portal into the native
 * settings dialog. Saved webhook URLs are never shown.
 */
export function DiscordAlertsPanel({
  discord,
}: {
  discord: DiscordWebhookSettings;
}) {
  const [confirm, setConfirm] = useState<DiscordWebhook | null>(null);
  const [portal, setPortal] = useState<HTMLElement | null>(null);
  const anchor = useCallback((node: HTMLDivElement | null) => {
    // Portal overlays into the native modal: <body> sits beneath its top layer.
    setPortal(node?.closest('dialog') ?? null);
  }, []);
  const { list, pending } = discord;
  const now = useRelativeNow(
    list?.webhooks.flatMap((item) => [item.last_sent_at, item.last_error_at]) ??
      [],
  );
  const full = list !== null && list.webhooks.length >= list.limit;
  const canAdd = list !== null && list.available && !full;
  const urlTyped = discord.url.trim() !== '';
  const urlInvalid = urlTyped && !isDiscordWebhookUrl(discord.url);
  const labelValid =
    discord.label.trim().length > 0 && discord.label.trim().length <= LABEL_MAX;

  return (
    <div ref={anchor} className="grid gap-4">
      <Card variant="secondary">
        <Card.Header>
          <Card.Title>Discord alerts</Card.Title>
          <Card.Description>
            Get a Discord message after each refresh that finds new jobs,
            listing the top 5 by pay. Add webhooks for as many servers or
            channels as you like.
          </Card.Description>
        </Card.Header>
        <Card.Content>
          <div className="grid gap-4">
            {list === null ? (
              <p role="status">{discord.message || 'Loading webhooks…'}</p>
            ) : (
              <>
                {!list.available && (
                  <Alert status="warning">
                    <Alert.Indicator />
                    <Alert.Content>
                      <Alert.Title>Discord alerts are unavailable</Alert.Title>
                      <Alert.Description>
                        The server is not set up to store webhooks securely yet.
                        Ask the administrator.
                      </Alert.Description>
                    </Alert.Content>
                  </Alert>
                )}
                {list.webhooks.length === 0 ? (
                  <Description>
                    No webhooks yet. Add one below to start getting alerts.
                  </Description>
                ) : (
                  <ul className="grid gap-3" aria-label="Your Discord webhooks">
                    {list.webhooks.map((webhook) => {
                      const shown = status(webhook, now);
                      return (
                        <li key={webhook.id}>
                          <Card>
                            <Card.Content>
                              <div className="grid gap-3">
                                <div className="flex flex-wrap items-center gap-2">
                                  <strong className="min-w-0 [overflow-wrap:anywhere]">
                                    {webhook.label}
                                  </strong>
                                  <Typography type="code">
                                    {webhook.hint}
                                  </Typography>
                                  {shown.chip}
                                </div>
                                {shown.detail && (
                                  <Description>{shown.detail}</Description>
                                )}
                                <div className="flex flex-wrap items-center justify-between gap-3">
                                  <Switch
                                    aria-label={`Alerts for ${webhook.label}`}
                                    isSelected={webhook.enabled}
                                    isDisabled={pending}
                                    onChange={(enabled) =>
                                      void discord.setEnabled(webhook, enabled)
                                    }
                                  >
                                    <Switch.Content>
                                      <Switch.Control>
                                        <Switch.Thumb />
                                      </Switch.Control>
                                      {webhook.enabled
                                        ? 'Alerts on'
                                        : 'Alerts off'}
                                    </Switch.Content>
                                  </Switch>
                                  <div className="flex flex-wrap gap-2">
                                    <Button
                                      size="sm"
                                      variant="secondary"
                                      aria-label={`Test ${webhook.label}`}
                                      isDisabled={pending}
                                      onPress={() => void discord.test(webhook)}
                                    >
                                      <LuSend aria-hidden="true" />
                                      Test
                                    </Button>
                                    <Button
                                      size="sm"
                                      variant="danger-soft"
                                      aria-label={`Delete ${webhook.label}`}
                                      isDisabled={pending}
                                      onPress={() => setConfirm(webhook)}
                                    >
                                      <LuTrash2 aria-hidden="true" />
                                      Delete
                                    </Button>
                                  </div>
                                </div>
                              </div>
                            </Card.Content>
                          </Card>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </>
            )}
            <p role="status">{list === null ? '' : discord.message}</p>
            <Description>
              To create one in Discord: Server Settings → Integrations →
              Webhooks → New Webhook → Copy Webhook URL.
            </Description>
            <Form
              aria-label="Add a Discord webhook"
              autoComplete="off"
              onSubmit={(event) => {
                event.preventDefault();
                if (canAdd && labelValid && !urlInvalid && urlTyped)
                  void discord.add();
              }}
            >
              <div className="grid gap-4">
                <TextField
                  fullWidth
                  value={discord.label}
                  onChange={discord.setLabel}
                  isDisabled={pending || !canAdd}
                >
                  <Label>Name</Label>
                  <Input
                    autoComplete="off"
                    maxLength={LABEL_MAX}
                    placeholder="e.g. Co-op channel"
                  />
                </TextField>
                <TextField
                  fullWidth
                  value={discord.url}
                  onChange={discord.setUrl}
                  isDisabled={pending || !canAdd}
                  isInvalid={urlInvalid}
                >
                  <Label>Webhook URL</Label>
                  <Input
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    autoCapitalize="none"
                    maxLength={300}
                    placeholder="https://discord.com/api/webhooks/…"
                  />
                  {urlInvalid ? (
                    <FieldError>
                      This isn’t a Discord webhook URL. It starts with
                      https://discord.com/api/webhooks/.
                    </FieldError>
                  ) : (
                    <Description>
                      Stored encrypted and never shown again, because anyone
                      with it can post to your channel.
                    </Description>
                  )}
                </TextField>
                {full && (
                  <Description>
                    You have the maximum of {list.limit} webhooks. Delete one to
                    add another.
                  </Description>
                )}
                <div>
                  <Button
                    type="submit"
                    isPending={pending && list !== null}
                    isDisabled={
                      pending ||
                      !canAdd ||
                      !labelValid ||
                      !urlTyped ||
                      urlInvalid
                    }
                  >
                    <LuPlus aria-hidden="true" />
                    Add webhook
                  </Button>
                </div>
              </div>
            </Form>
          </div>
        </Card.Content>
      </Card>
      <Accordion>
        <Accordion.Item id="discord-help">
          <Accordion.Heading>
            <Accordion.Trigger>
              How to set it up
              <Accordion.Indicator />
            </Accordion.Trigger>
          </Accordion.Heading>
          <Accordion.Panel>
            <Accordion.Body>
              <div className="grid gap-2">
                <Description>
                  1. In Discord, open your server’s settings and choose
                  Integrations, then Webhooks. You need the Manage Webhooks
                  permission.
                </Description>
                <Description>
                  2. Choose New Webhook, pick the channel, then Copy Webhook
                  URL.
                </Description>
                <Description>
                  3. Paste it here, give it a name and send a test.
                </Description>
                <Description>
                  After each refresh that finds new jobs, every webhook that is
                  on gets one message with the top 5 new jobs by pay; the rest
                  are counted at the bottom. Turn a webhook off to pause it.
                </Description>
              </div>
            </Accordion.Body>
          </Accordion.Panel>
        </Accordion.Item>
      </Accordion>
      {portal && (
        <AlertDialog.Backdrop
          isOpen={confirm !== null}
          onOpenChange={(open) => {
            if (!open) setConfirm(null);
          }}
          UNSTABLE_portalContainer={portal}
        >
          <AlertDialog.Container size="sm">
            <AlertDialog.Dialog>
              <AlertDialog.Header>
                <AlertDialog.Icon status="danger" />
                <AlertDialog.Heading>Delete this webhook?</AlertDialog.Heading>
              </AlertDialog.Header>
              <AlertDialog.Body>
                <p>
                  “{confirm?.label}” stops getting alerts. The webhook itself
                  stays in Discord until you delete it there.
                </p>
              </AlertDialog.Body>
              <AlertDialog.Footer>
                <Button slot="close" variant="tertiary">
                  Cancel
                </Button>
                <Button
                  variant="danger"
                  onPress={() => {
                    const target = confirm;
                    setConfirm(null);
                    if (target) void discord.remove(target);
                  }}
                >
                  Delete webhook
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      )}
    </div>
  );
}
