import { useCallback, useState, type ReactNode } from 'react';
import { LuCheck, LuUserRound, LuUserX } from 'react-icons/lu';
import {
  AlertDialog,
  Avatar,
  Button,
  Card,
  Chip,
  Description,
} from '@heroui/react';
import {
  displayLabel,
  type ApprovalSettings,
  type ApprovalTreeSettings,
  type DeclinedUser,
  type PendingUser,
  type TreeUser,
} from '../lib/useApprovals';
import { relativeTime, torontoDateTime } from '../lib/refreshSchedule';
import { useRelativeNow } from '../lib/useRelativeNow';

function when(label: string, value: string | null, now: number) {
  if (!value) return null;
  return `${label} ${relativeTime(Date.parse(value), now)} · ${torontoDateTime(value)}`;
}

// Documented HeroUI Avatar composition; the photo host is validated upstream.
function Person({
  photo,
  name,
  children,
}: {
  photo: string | null;
  name: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex min-w-0 items-start gap-3">
      <Avatar size="sm">
        {photo && (
          <Avatar.Image src={photo} alt="" referrerPolicy="no-referrer" />
        )}
        <Avatar.Fallback>
          <LuUserRound aria-hidden="true" />
        </Avatar.Fallback>
      </Avatar>
      <div className="grid min-w-0 gap-0.5">
        <strong className="min-w-0 [overflow-wrap:anywhere]">{name}</strong>
        {children}
      </div>
    </div>
  );
}

type Confirm =
  | { kind: 'decline'; person: PendingUser }
  | { kind: 'remove'; person: TreeUser }
  | null;

/**
 * Documented HeroUI Card/Avatar/Button/Chip/AlertDialog composition for the
 * admission tab: who is waiting, who the user approved and, for the
 * administrator, the whole tree with removal and reinstatement. Nested
 * native lists only lay the tree out; overlays portal into the native
 * settings dialog.
 */
export function ApprovalsPanel({
  approvals,
  tree,
}: {
  approvals: ApprovalSettings;
  /** Administrator only. */
  tree?: ApprovalTreeSettings;
}) {
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [portal, setPortal] = useState<HTMLElement | null>(null);
  const anchor = useCallback((node: HTMLDivElement | null) => {
    // Portal overlays into the native modal: <body> sits beneath its top layer.
    setPortal(node?.closest('dialog') ?? null);
  }, []);
  const { listing, pending } = approvals;
  const now = useRelativeNow([
    ...(listing?.pending.map((person) => person.requested_at) ?? []),
    ...(listing?.approved_by_me.map((person) => person.approved_at) ?? []),
    ...(tree?.tree?.declined.map((person) => person.decided_at) ?? []),
  ]);
  const busy = pending || (tree?.pending ?? false);

  const node = (person: TreeUser): ReactNode => (
    <li key={person.id} className="grid gap-2">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <Person photo={person.photo_url} name={displayLabel(person)}>
          {person.email && person.display_name && (
            <Description className="[overflow-wrap:anywhere]">
              {person.email}
            </Description>
          )}
          {person.role === 'admin' ? (
            <div>
              <Chip size="sm" variant="soft" color="accent">
                Administrator
              </Chip>
            </div>
          ) : (
            <Description>
              {person.approved_by
                ? `Approved by ${person.approved_by}`
                : 'Approved by the administrator'}
              {person.approved_at
                ? ` · ${relativeTime(Date.parse(person.approved_at), now)} · ${torontoDateTime(person.approved_at)}`
                : ''}
            </Description>
          )}
        </Person>
        {person.role !== 'admin' && tree && (
          <Button
            size="sm"
            variant="danger-soft"
            aria-label={`Remove ${displayLabel(person)}`}
            isDisabled={busy}
            onPress={() => setConfirm({ kind: 'remove', person })}
          >
            <LuUserX aria-hidden="true" />
            Remove…
          </Button>
        )}
      </div>
      {person.children.length > 0 && (
        <ul
          className="ms-4 grid gap-3 border-s border-border ps-4"
          aria-label={`Approved by ${displayLabel(person)}`}
        >
          {person.children.map(node)}
        </ul>
      )}
    </li>
  );

  return (
    <div ref={anchor} className="grid gap-4">
      <Card variant="secondary">
        <Card.Header>
          <Card.Title>Waiting for approval</Card.Title>
          <Card.Description>
            New sign-ins wait here until someone who already uses the app
            approves them. Only approve people you know; they will be listed
            under you.
          </Card.Description>
        </Card.Header>
        <Card.Content>
          <div className="grid gap-4">
            {listing === null ? (
              <p role="status">{approvals.message || 'Loading approvals…'}</p>
            ) : listing.pending.length === 0 ? (
              <Description>
                Nobody is waiting right now. New sign-ins appear here.
              </Description>
            ) : (
              <ul
                className="grid gap-3"
                aria-label="People waiting for approval"
              >
                {listing.pending.map((person) => (
                  <li key={person.id}>
                    <Card>
                      <Card.Content>
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <Person
                            photo={person.photo_url}
                            name={displayLabel(person)}
                          >
                            {person.email && person.display_name && (
                              <Description className="[overflow-wrap:anywhere]">
                                {person.email}
                              </Description>
                            )}
                            <Description>
                              {when('Requested', person.requested_at, now)}
                            </Description>
                          </Person>
                          <div className="flex flex-wrap gap-2">
                            <Button
                              size="sm"
                              aria-label={`Approve ${displayLabel(person)}`}
                              isDisabled={busy}
                              onPress={() => void approvals.approve(person)}
                            >
                              <LuCheck aria-hidden="true" />
                              Approve
                            </Button>
                            <Button
                              size="sm"
                              variant="danger-soft"
                              aria-label={`Decline ${displayLabel(person)}`}
                              isDisabled={busy}
                              onPress={() =>
                                setConfirm({ kind: 'decline', person })
                              }
                            >
                              Decline…
                            </Button>
                          </div>
                        </div>
                      </Card.Content>
                    </Card>
                  </li>
                ))}
              </ul>
            )}
            {listing !== null && <p role="status">{approvals.message}</p>}
          </div>
        </Card.Content>
      </Card>
      <Card variant="secondary">
        <Card.Header>
          <Card.Title>Your approvals</Card.Title>
          <Card.Description>
            People you approved who still have access.
          </Card.Description>
        </Card.Header>
        <Card.Content>
          {listing === null ? (
            <p role="status">Loading…</p>
          ) : listing.approved_by_me.length === 0 ? (
            <Description>You have not approved anyone yet.</Description>
          ) : (
            <ul className="grid gap-3" aria-label="People you approved">
              {listing.approved_by_me.map((person) => (
                <li key={person.id}>
                  <Person photo={person.photo_url} name={displayLabel(person)}>
                    <Description>
                      {when('Approved', person.approved_at, now)}
                    </Description>
                  </Person>
                </li>
              ))}
            </ul>
          )}
        </Card.Content>
      </Card>
      {tree && (
        <>
          <Card variant="secondary">
            <Card.Header>
              <Card.Title>Everyone with access</Card.Title>
              <Card.Description>
                Who approved whom. Removing someone ends their access at once
                and moves the people they approved up under their own approver.
              </Card.Description>
            </Card.Header>
            <Card.Content>
              <div className="grid gap-4">
                {tree.tree === null ? (
                  <p role="status">{tree.message || 'Loading the tree…'}</p>
                ) : tree.tree.root === null ? (
                  <Description>No administrator yet.</Description>
                ) : (
                  <ul className="grid gap-3" aria-label="Approval tree">
                    {node(tree.tree.root)}
                  </ul>
                )}
                {tree.tree !== null && <p role="status">{tree.message}</p>}
              </div>
            </Card.Content>
          </Card>
          <Card variant="secondary">
            <Card.Header>
              <Card.Title>Declined and removed</Card.Title>
              <Card.Description>
                These accounts see a fixed “not approved” message when they sign
                in. Moving someone back to pending lets any approved user admit
                them again.
              </Card.Description>
            </Card.Header>
            <Card.Content>
              {tree.tree === null ? (
                <p role="status">Loading…</p>
              ) : tree.tree.declined.length === 0 ? (
                <Description>Nobody has been declined or removed.</Description>
              ) : (
                <ul
                  className="grid gap-3"
                  aria-label="Declined and removed people"
                >
                  {tree.tree.declined.map((person: DeclinedUser) => (
                    <li key={person.id}>
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <Person
                          photo={person.photo_url}
                          name={displayLabel(person)}
                        >
                          {person.email && person.display_name && (
                            <Description className="[overflow-wrap:anywhere]">
                              {person.email}
                            </Description>
                          )}
                          <div className="flex flex-wrap items-center gap-2">
                            <Chip
                              size="sm"
                              variant="soft"
                              color={
                                person.status === 'removed'
                                  ? 'danger'
                                  : 'warning'
                              }
                            >
                              {person.status === 'removed'
                                ? 'Removed'
                                : 'Declined'}
                            </Chip>
                            <Description>
                              {person.decided_by
                                ? `by ${person.decided_by} · `
                                : ''}
                              {relativeTime(Date.parse(person.decided_at), now)}{' '}
                              · {torontoDateTime(person.decided_at)}
                            </Description>
                          </div>
                        </Person>
                        <Button
                          size="sm"
                          variant="secondary"
                          aria-label={`Move ${displayLabel(person)} back to pending`}
                          isDisabled={busy}
                          onPress={() => void tree.reinstate(person)}
                        >
                          Move back to pending
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Card.Content>
          </Card>
        </>
      )}
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
                <AlertDialog.Heading>
                  {confirm?.kind === 'remove'
                    ? `Remove ${displayLabel(confirm.person)}?`
                    : confirm
                      ? `Decline ${displayLabel(confirm.person)}?`
                      : ''}
                </AlertDialog.Heading>
              </AlertDialog.Header>
              <AlertDialog.Body>
                <p>
                  {confirm?.kind === 'remove'
                    ? 'They lose access immediately: their sessions, API token, Discord alerts and marks are deleted. Anyone they approved keeps access and moves up under their approver. Only you can move them back to pending.'
                    : 'They will not be able to use the app, and signing in again shows them a fixed “not approved” message. Only the administrator can move them back to pending.'}
                </p>
              </AlertDialog.Body>
              <AlertDialog.Footer>
                <Button slot="close" variant="tertiary">
                  Cancel
                </Button>
                <Button
                  variant="danger"
                  onPress={() => {
                    const action = confirm;
                    setConfirm(null);
                    if (!action) return;
                    if (action.kind === 'remove')
                      void tree?.remove(action.person);
                    else void approvals.decline(action.person);
                  }}
                >
                  {confirm?.kind === 'remove' ? 'Remove access' : 'Decline'}
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      )}
    </div>
  );
}
