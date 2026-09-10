import { useCallback, useEffect, useRef, useState } from 'react';
import { AuthGeneration, runAuthOperation } from './authLifecycle';
import type { components } from './api.generated';
import { SECRET_MASK } from './openaiRevealState';

type CredentialStatus = components['schemas']['CredentialStatus'];
const endpoint = '/api/admin/waterlooworks-credentials';

export function parseCredentialStatus(value: unknown): CredentialStatus {
  if (!value || typeof value !== 'object')
    throw new Error('Settings unavailable.');
  const data = value as Record<string, unknown>;
  if (
    Object.keys(data).some(
      (key) => !['configured', 'status', 'updated_at'].includes(key),
    ) ||
    typeof data.configured !== 'boolean' ||
    data.status !== (data.configured ? 'configured' : 'not_configured') ||
    (data.configured
      ? typeof data.updated_at !== 'string' ||
        data.updated_at.length > 64 ||
        !Number.isFinite(Date.parse(data.updated_at))
      : data.updated_at !== null)
  )
    throw new Error('Settings unavailable.');
  return {
    configured: data.configured,
    status: data.configured ? 'configured' : 'not_configured',
    updated_at: data.updated_at as string | null,
  };
}

export function parseAdminCredentialStatus(value: unknown) {
  if (!value || typeof value !== 'object' || !('username' in value))
    throw new Error('Settings unavailable.');
  const { username, ...metadata } = value;
  const status = parseCredentialStatus(metadata);
  if (
    status.configured
      ? typeof username !== 'string' ||
        !username.trim() ||
        username.length > 256
      : username !== null
  )
    throw new Error('Settings unavailable.');
  return { ...status, username: username as string | null };
}

// Admin-only caller. Tab memory only; each open/save owns a bounded generation.
export function useAdminCredentials(uid: string) {
  const lifecycle = useRef(new AuthGeneration());
  const busy = useRef(false);
  const [isOpen, setIsOpen] = useState(false);
  const [username, setUsername] = useState('');
  const [savedUsername, setSavedUsername] = useState<string | null>(null);
  const [configured, setConfigured] = useState(false);
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');

  const clear = useCallback(() => {
    lifecycle.current.begin();
    busy.current = false;
    setUsername('');
    setSavedUsername(null);
    setConfigured(false);
    setPassword('');
    setPending(false);
    setMessage('');
    setIsOpen(false);
  }, []);

  useEffect(() => clear, [uid, clear]);

  const request = async (body?: { username: string; password: string }) => {
    const generation = lifecycle.current.begin();
    busy.current = true;
    setPending(true);
    setMessage(body ? 'Saving…' : 'Loading credential status…');
    try {
      const status = await runAuthOperation(async (operation) => {
        const response = await operation.wait(
          fetch(endpoint, {
            method: body ? 'POST' : 'GET',
            body: body ? JSON.stringify(body) : undefined,
            headers: body
              ? { 'Content-Type': 'application/json', 'X-WW-Request': '1' }
              : undefined,
            credentials: 'same-origin',
            cache: 'no-store',
            signal: operation.signal,
          }),
        );
        if (!response.ok) throw new Error('Settings unavailable.');
        const data: unknown = await operation.wait(response.json());
        return body
          ? { ...parseCredentialStatus(data), username: body.username }
          : parseAdminCredentialStatus(data);
      }, lifecycle.current.signal);
      if (!lifecycle.current.current(generation)) return;
      setSavedUsername(status.username);
      setConfigured(status.configured);
      if (body) {
        setUsername('');
        setPassword('');
        setMessage('Saved. WaterlooWorks login has not been tested.');
      } else {
        setMessage(
          status.configured
            ? 'Credentials configured.'
            : 'Credentials not configured.',
        );
      }
    } catch {
      if (lifecycle.current.current(generation)) {
        setMessage(
          body
            ? 'Save could not be confirmed. It may have reached the server; reopen settings to check status before retrying.'
            : 'Credential settings unavailable. Check your session and the backend encryption key.',
        );
      }
    } finally {
      if (lifecycle.current.current(generation)) {
        busy.current = false;
        setPending(false);
      }
    }
  };

  return {
    isOpen,
    username,
    savedUsername,
    configured,
    password,
    pending,
    message,
    setUsername,
    setPassword,
    setOpen: (open: boolean) => {
      clear();
      if (open) {
        setIsOpen(true);
        void request();
      }
    },
    save: async () => {
      if (
        !isOpen ||
        busy.current ||
        !username.trim() ||
        username.length > 256 ||
        !password ||
        password === SECRET_MASK ||
        password.length > 1024
      )
        return;
      await request({ username, password });
    },
  };
}
