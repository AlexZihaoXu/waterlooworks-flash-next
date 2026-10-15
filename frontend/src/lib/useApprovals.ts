import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AuthGeneration,
  parseRetryAfter,
  runAuthOperation,
} from './authLifecycle';
import { googlePhotoUrl } from './accountProfile';
import type { components } from './api.generated';

export type PendingUser = components['schemas']['PendingUser'];
export type ApprovedUser = components['schemas']['ApprovedUser'];
export type ApprovalListing = components['schemas']['ApprovalListing'];
export type TreeUser = components['schemas']['TreeUser'];
export type DeclinedUser = components['schemas']['DeclinedUser'];
export type ApprovalTree = components['schemas']['ApprovalTree'];

const endpoint = '/api/approvals';
const adminEndpoint = '/api/admin/approvals';
const ID = /^[A-Za-z0-9._:-]{1,128}$/;

const isTime = (value: unknown) =>
  typeof value === 'string' &&
  value.length <= 64 &&
  !Number.isNaN(Date.parse(value));

function text(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 320) return null;
  const trimmed = value.trim();
  const control = Array.from(trimmed).some((char) => {
    const code = char.charCodeAt(0);
    return code < 32 || code === 127;
  });
  return trimmed && !control ? trimmed : null;
}

function keysOf(value: unknown, allowed: readonly string[]) {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Approvals unavailable.');
  const data = value as Record<string, unknown>;
  if (Object.keys(data).some((key) => !allowed.includes(key)))
    throw new Error('Approvals unavailable.');
  return data;
}

function id(value: unknown): string {
  if (typeof value !== 'string' || !ID.test(value))
    throw new Error('Approvals unavailable.');
  return value;
}

export function parsePendingUser(value: unknown): PendingUser {
  const data = keysOf(value, [
    'id',
    'display_name',
    'email',
    'photo_url',
    'requested_at',
  ]);
  if (!isTime(data.requested_at)) throw new Error('Approvals unavailable.');
  return {
    id: id(data.id),
    display_name: text(data.display_name),
    email: text(data.email),
    photo_url: googlePhotoUrl(data.photo_url),
    requested_at: data.requested_at as string,
  };
}

export function parseApprovedUser(value: unknown): ApprovedUser {
  const data = keysOf(value, [
    'id',
    'display_name',
    'photo_url',
    'approved_at',
  ]);
  if (!isTime(data.approved_at)) throw new Error('Approvals unavailable.');
  return {
    id: id(data.id),
    display_name: text(data.display_name),
    photo_url: googlePhotoUrl(data.photo_url),
    approved_at: data.approved_at as string,
  };
}

export function parseApprovalListing(value: unknown): ApprovalListing {
  const data = keysOf(value, ['pending', 'approved_by_me']);
  if (!Array.isArray(data.pending) || !Array.isArray(data.approved_by_me))
    throw new Error('Approvals unavailable.');
  return {
    pending: data.pending.map(parsePendingUser),
    approved_by_me: data.approved_by_me.map(parseApprovedUser),
  };
}

export function parseTreeUser(value: unknown, depth = 0): TreeUser {
  if (depth > 64) throw new Error('Approvals unavailable.');
  const data = keysOf(value, [
    'id',
    'display_name',
    'email',
    'photo_url',
    'role',
    'approved_at',
    'approved_by',
    'children',
  ]);
  if (
    !['admin', 'member'].includes(String(data.role)) ||
    !(data.approved_at === null || isTime(data.approved_at)) ||
    !Array.isArray(data.children)
  )
    throw new Error('Approvals unavailable.');
  return {
    id: id(data.id),
    display_name: text(data.display_name),
    email: text(data.email),
    photo_url: googlePhotoUrl(data.photo_url),
    role: data.role as TreeUser['role'],
    approved_at: (data.approved_at as string | null) ?? null,
    approved_by: text(data.approved_by),
    children: data.children.map((child) => parseTreeUser(child, depth + 1)),
  };
}

export function parseDeclinedUser(value: unknown): DeclinedUser {
  const data = keysOf(value, [
    'id',
    'display_name',
    'email',
    'photo_url',
    'status',
    'decided_at',
    'decided_by',
  ]);
  if (
    !['declined', 'removed'].includes(String(data.status)) ||
    !isTime(data.decided_at)
  )
    throw new Error('Approvals unavailable.');
  return {
    id: id(data.id),
    display_name: text(data.display_name),
    email: text(data.email),
    photo_url: googlePhotoUrl(data.photo_url),
    status: data.status as DeclinedUser['status'],
    decided_at: data.decided_at as string,
    decided_by: text(data.decided_by),
  };
}

export function parseApprovalTree(value: unknown): ApprovalTree {
  const data = keysOf(value, ['root', 'declined']);
  if (!Array.isArray(data.declined)) throw new Error('Approvals unavailable.');
  return {
    root: data.root === null ? null : parseTreeUser(data.root),
    declined: data.declined.map(parseDeclinedUser),
  };
}

class ApprovalHttpError extends Error {
  readonly status: number;
  readonly retryAfter: number | null;
  constructor(status: number, retryAfter: number | null) {
    super('Approval request refused.');
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

function request(
  path: string,
  signal: AbortSignal,
  method: 'GET' | 'POST' = 'GET',
): Promise<unknown> {
  return runAuthOperation(async (operation) => {
    const response = await operation.wait(
      fetch(path, {
        method,
        body: method === 'GET' ? undefined : '{}',
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
      throw new ApprovalHttpError(
        response.status,
        parseRetryAfter(response.headers.get('Retry-After')),
      );
    return operation.wait(response.json());
  }, signal);
}

/** Fixed wording per refusal; never the server's text. */
export function refusalMessage(error: unknown, action: string): string {
  if (error instanceof ApprovalHttpError) {
    if (error.status === 409)
      return 'Someone else already decided this request. The list was refreshed.';
    if (error.status === 404)
      return 'That person is no longer listed. The list was refreshed.';
    if (error.status === 429) {
      const minutes = Math.max(1, Math.ceil((error.retryAfter ?? 60) / 60));
      return `Too many changes in a short time. Try again in about ${minutes} minute${minutes === 1 ? '' : 's'}.`;
    }
  }
  return `The ${action} could not be confirmed. Reopen settings to check.`;
}

export function displayLabel(user: {
  display_name: string | null;
  email?: string | null;
}): string {
  return user.display_name || user.email || 'Unnamed account';
}

/**
 * Who is waiting for approval and who the signed-in user approved. Loaded
 * once per account on mount (for the pending badge), again whenever the
 * Approvals section opens, and after each decision. Nothing is persisted.
 */
export function useApprovals(uid: string, active: boolean) {
  const lifecycle = useRef(new AuthGeneration());
  const busy = useRef(false);
  const [listing, setListing] = useState<ApprovalListing | null>(null);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');

  const load = useCallback((keepMessage = false) => {
    const lifetime = lifecycle.current;
    const generation = lifetime.begin();
    busy.current = true;
    setPending(true);
    if (!keepMessage) setMessage('');
    void request(endpoint, lifetime.signal)
      .then(parseApprovalListing)
      .then((loaded) => {
        if (lifetime.current(generation)) setListing(loaded);
      })
      .catch(() => {
        if (lifetime.current(generation))
          setMessage('Approvals are unavailable. Reopen settings to retry.');
      })
      .finally(() => {
        if (lifetime.current(generation)) {
          busy.current = false;
          setPending(false);
        }
      });
  }, []);

  useEffect(() => {
    if (!uid) return;
    queueMicrotask(() => load());
    const lifetime = lifecycle.current;
    return () => {
      lifetime.begin();
      busy.current = false;
      setListing(null);
      setPending(false);
      setMessage('');
    };
  }, [uid, load]);

  useEffect(() => {
    if (active && uid) queueMicrotask(() => load());
  }, [active, uid, load]);

  const decide = async (person: PendingUser, action: 'approve' | 'decline') => {
    if (!active || busy.current) return;
    const lifetime = lifecycle.current;
    const generation = lifetime.begin();
    busy.current = true;
    setPending(true);
    setMessage('');
    try {
      const updated = parseApprovalListing(
        await request(
          `${endpoint}/${person.id}/${action}`,
          lifetime.signal,
          'POST',
        ),
      );
      if (!lifetime.current(generation)) return;
      setListing(updated);
      setMessage(
        action === 'approve'
          ? `${displayLabel(person)} can use the app now.`
          : `${displayLabel(person)} was declined.`,
      );
    } catch (error) {
      if (!lifetime.current(generation)) return;
      setMessage(refusalMessage(error, action));
      if (
        error instanceof ApprovalHttpError &&
        (error.status === 404 || error.status === 409)
      )
        load(true);
    } finally {
      if (lifetime.current(generation)) {
        busy.current = false;
        setPending(false);
      }
    }
  };

  return {
    listing,
    pendingCount: listing?.pending.length ?? 0,
    pending,
    message,
    approve: (person: PendingUser) => decide(person, 'approve'),
    decline: (person: PendingUser) => decide(person, 'decline'),
    reload: () => load(),
  };
}

export type ApprovalSettings = ReturnType<typeof useApprovals>;

/**
 * Administrator only: the whole tree with remove and move-back-to-pending.
 * Loaded while the Approvals section is open; cleared when it closes.
 */
export function useApprovalTree(active: boolean, uid: string) {
  const lifecycle = useRef(new AuthGeneration());
  const busy = useRef(false);
  const [tree, setTree] = useState<ApprovalTree | null>(null);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');

  const load = useCallback((keepMessage = false) => {
    const lifetime = lifecycle.current;
    const generation = lifetime.begin();
    busy.current = true;
    setPending(true);
    if (!keepMessage) setMessage('');
    void request(adminEndpoint, lifetime.signal)
      .then(parseApprovalTree)
      .then((loaded) => {
        if (lifetime.current(generation)) setTree(loaded);
      })
      .catch(() => {
        if (lifetime.current(generation))
          setMessage('The tree is unavailable. Reopen settings to retry.');
      })
      .finally(() => {
        if (lifetime.current(generation)) {
          busy.current = false;
          setPending(false);
        }
      });
  }, []);

  useEffect(() => {
    if (!active) return;
    queueMicrotask(() => load());
    const lifetime = lifecycle.current;
    return () => {
      lifetime.begin();
      busy.current = false;
      setTree(null);
      setPending(false);
      setMessage('');
    };
  }, [active, uid, load]);

  const change = async (
    person: { id: string; display_name: string | null; email?: string | null },
    action: 'remove' | 'reinstate',
  ) => {
    if (!active || busy.current) return;
    const lifetime = lifecycle.current;
    const generation = lifetime.begin();
    busy.current = true;
    setPending(true);
    setMessage('');
    try {
      const updated = parseApprovalTree(
        await request(
          `${adminEndpoint}/${person.id}/${action}`,
          lifetime.signal,
          'POST',
        ),
      );
      if (!lifetime.current(generation)) return;
      setTree(updated);
      setMessage(
        action === 'remove'
          ? `${displayLabel(person)} no longer has access.`
          : `${displayLabel(person)} is waiting for approval again.`,
      );
    } catch (error) {
      if (!lifetime.current(generation)) return;
      setMessage(refusalMessage(error, action));
      if (
        error instanceof ApprovalHttpError &&
        (error.status === 404 || error.status === 409)
      )
        load(true);
    } finally {
      if (lifetime.current(generation)) {
        busy.current = false;
        setPending(false);
      }
    }
  };

  return {
    tree,
    pending,
    message,
    remove: (person: TreeUser) => change(person, 'remove'),
    reinstate: (person: DeclinedUser) => change(person, 'reinstate'),
    reload: () => load(),
  };
}

export type ApprovalTreeSettings = ReturnType<typeof useApprovalTree>;
