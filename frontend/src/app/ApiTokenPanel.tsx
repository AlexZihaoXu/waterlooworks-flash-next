import { useCallback, useRef, useState } from 'react';
import { LuCopy, LuKeyRound } from 'react-icons/lu';
import {
  Alert,
  AlertDialog,
  Button,
  Card,
  Chip,
  Description,
  Disclosure,
  Input,
  Label,
  TextField,
  Typography,
} from '@heroui/react';
import type { ApiTokenSettings } from '../lib/useApiToken';
import { relativeTime, torontoDateTime } from '../lib/refreshSchedule';
import { useRelativeNow } from '../lib/useRelativeNow';

function when(label: string, value: string | null | undefined, now: number) {
  if (!value) return null;
  return `${label} ${relativeTime(Date.parse(value), now)} · ${torontoDateTime(value)}`;
}

type Confirm = 'reset' | 'revoke' | null;

/**
 * Documented HeroUI Card/Alert/TextField/AlertDialog composition for the
 * personal agent API token. Overlays portal into the native settings dialog.
 */
export function ApiTokenPanel({ token }: { token: ApiTokenSettings }) {
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [portal, setPortal] = useState<HTMLElement | null>(null);
  const field = useRef<HTMLInputElement>(null);
  const anchor = useCallback((node: HTMLDivElement | null) => {
    // Portal overlays into the native modal: <body> sits beneath its top layer.
    setPortal(node?.closest('dialog') ?? null);
  }, []);
  const { status, issued, pending, copied } = token;
  const now = useRelativeNow([status?.created_at, status?.last_used_at]);
  const origin = window.location.origin;

  const copy = async () => {
    if (!issued) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('No clipboard.');
      await navigator.clipboard.writeText(issued.token);
      token.setCopied(true);
      token.setMessage('Copied to clipboard.');
    } catch {
      // Fallback: select the read-only field so the user can copy manually.
      field.current?.focus();
      field.current?.select();
      field.current?.setSelectionRange(0, issued.token.length);
      token.setMessage(
        'Token selected. Copy it with Ctrl+C (⌘C on Mac), or long-press on a phone.',
      );
    }
  };

  return (
    <div ref={anchor} className="grid gap-4">
      <Card variant="secondary">
        <Card.Header>
          <Card.Title>Personal API token</Card.Title>
          <Card.Description>
            Lets tools such as AI agents search, read and mark postings as you.
            It cannot refresh postings or change any settings.
          </Card.Description>
        </Card.Header>
        <Card.Content>
          <div className="grid gap-4">
            {status === null ? (
              <p role="status">{token.message || 'Loading token status…'}</p>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Chip
                    size="sm"
                    variant="soft"
                    color={status.configured ? 'success' : 'default'}
                  >
                    {status.configured ? 'Active' : 'No token yet'}
                  </Chip>
                  {status.configured && status.hint && (
                    <Typography type="code">{status.hint}</Typography>
                  )}
                </div>
                {status.configured && (
                  <div className="grid gap-1">
                    <Description>
                      {when('Created', status.created_at, now)}
                    </Description>
                    <Description>
                      {when('Last used', status.last_used_at, now) ??
                        'Never used'}
                    </Description>
                  </div>
                )}
                {issued && (
                  <Alert status="warning">
                    <Alert.Indicator />
                    <Alert.Content>
                      <Alert.Title>Copy your new token now</Alert.Title>
                      <Alert.Description>
                        You won’t be able to see it again. It is cleared when
                        you hide it, close settings, switch browser tabs or
                        after 5 minutes.
                      </Alert.Description>
                    </Alert.Content>
                  </Alert>
                )}
                {issued && (
                  <div className="grid gap-2">
                    <TextField fullWidth isReadOnly value={issued.token}>
                      <Label>New API token</Label>
                      <Input
                        ref={field}
                        autoComplete="off"
                        spellCheck={false}
                        autoCapitalize="none"
                      />
                    </TextField>
                    <div className="flex flex-wrap gap-2">
                      <Button onPress={() => void copy()}>
                        <LuCopy aria-hidden="true" />
                        {copied ? 'Copy again' : 'Copy token'}
                      </Button>
                      <Button
                        variant="tertiary"
                        onPress={() => {
                          token.hide();
                          token.setMessage('');
                        }}
                      >
                        Hide token
                      </Button>
                    </div>
                  </div>
                )}
                <p role="status">{token.message}</p>
                <div className="flex flex-wrap gap-2">
                  {status.configured ? (
                    <>
                      <Button
                        variant="secondary"
                        isDisabled={pending}
                        onPress={() => setConfirm('reset')}
                      >
                        Reset token…
                      </Button>
                      <Button
                        variant="danger-soft"
                        isDisabled={pending}
                        onPress={() => setConfirm('revoke')}
                      >
                        Revoke…
                      </Button>
                    </>
                  ) : (
                    <Button
                      isPending={pending}
                      isDisabled={pending}
                      onPress={() => void token.reset()}
                    >
                      <LuKeyRound aria-hidden="true" />
                      Create token
                    </Button>
                  )}
                </div>
              </>
            )}
          </div>
        </Card.Content>
      </Card>
      <Disclosure>
        <Disclosure.Heading>
          <Disclosure.Trigger>
            How to use it
            <Disclosure.Indicator />
          </Disclosure.Trigger>
        </Disclosure.Heading>
        <Disclosure.Content>
          <Disclosure.Body>
            <div className="grid gap-2">
              <Description>
                Send it as a bearer token to the agent API. Keep it secret: it
                acts as you. One token per account; resetting or revoking it
                stops the old one immediately.
              </Description>
              <div className="overflow-x-auto">
                <Typography type="code">
                  {`curl -sS ${origin}/api/agent/v1/postings?q=python -H 'Authorization: Bearer wwf_…'`}
                </Typography>
              </div>
              <Description>
                Full reference: docs/agent-api.md in the project repository.
              </Description>
            </div>
          </Disclosure.Body>
        </Disclosure.Content>
      </Disclosure>
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
                <AlertDialog.Icon
                  status={confirm === 'revoke' ? 'danger' : 'warning'}
                />
                <AlertDialog.Heading>
                  {confirm === 'revoke'
                    ? 'Revoke your API token?'
                    : 'Replace your API token?'}
                </AlertDialog.Heading>
              </AlertDialog.Header>
              <AlertDialog.Body>
                <p>
                  {confirm === 'revoke'
                    ? 'The current token stops working immediately. Tools using it will need a new token.'
                    : 'The current token stops working immediately. Tools using it must be updated with the new token.'}
                </p>
              </AlertDialog.Body>
              <AlertDialog.Footer>
                <Button slot="close" variant="tertiary">
                  Cancel
                </Button>
                <Button
                  variant={confirm === 'revoke' ? 'danger' : 'primary'}
                  onPress={() => {
                    const action = confirm;
                    setConfirm(null);
                    void (action === 'revoke' ? token.revoke() : token.reset());
                  }}
                >
                  {confirm === 'revoke' ? 'Revoke token' : 'Replace token'}
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      )}
    </div>
  );
}
