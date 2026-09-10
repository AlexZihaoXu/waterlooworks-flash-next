import { useCallback, useEffect, useRef, useState } from 'react';
import { AuthGeneration, runAuthOperation } from './authLifecycle';
import { parseCredentialStatus } from './useAdminCredentials';
import {
  scheduleRevealHide,
  SECRET_MASK,
  waitForInteractiveVerification,
} from './openaiRevealState';
import type { createGoogleStepUp } from './firebase';
import type { components } from './api.generated';

type StepUp = ReturnType<typeof createGoogleStepUp>;
type Challenge = components['schemas']['RevealChallenge'];
const endpoint = '/api/admin/openai-key';

export function parseRevealChallenge(value: unknown): Challenge {
  if (!value || typeof value !== 'object')
    throw new Error('Verification unavailable.');
  const data = value as Record<string, unknown>;
  if (
    Object.keys(data).length !== 3 ||
    typeof data.challenge !== 'string' ||
    !/^[A-Za-z0-9_-]{43}$/.test(data.challenge) ||
    typeof data.not_before !== 'number' ||
    !Number.isSafeInteger(data.not_before) ||
    typeof data.expires_at !== 'number' ||
    !Number.isSafeInteger(data.expires_at) ||
    data.expires_at <= data.not_before ||
    data.expires_at - data.not_before > 300
  )
    throw new Error('Verification unavailable.');
  return data as Challenge;
}

export function parseRevealedKey(value: unknown): string {
  if (
    !value ||
    typeof value !== 'object' ||
    Object.keys(value).length !== 1 ||
    !('api_key' in value) ||
    typeof value.api_key !== 'string' ||
    !value.api_key.trim() ||
    value.api_key.length > 512
  )
    throw new Error('Reveal unavailable.');
  return value.api_key;
}

function request(
  path: string,
  signal: AbortSignal,
  body?: object,
): Promise<unknown> {
  return runAuthOperation(async (operation) => {
    const response = await operation.wait(
      fetch(path, {
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
    return operation.wait(response.json());
  }, signal);
}

export function useOpenAISettings(isOpen: boolean, uid: string) {
  const lifecycle = useRef(new AuthGeneration());
  const busy = useRef(false);
  const client = useRef<StepUp | null>(null);
  const challenge = useRef<Challenge | null>(null);
  const expiryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [draft, setDraft] = useState('');
  const [revealed, setRevealed] = useState('');
  const [configured, setConfigured] = useState(false);
  const [pending, setPending] = useState(isOpen);
  const [readyToVerify, setReadyToVerify] = useState(false);
  const [message, setMessage] = useState('');

  const hide = useCallback(() => {
    lifecycle.current.begin();
    client.current?.dispose();
    client.current = null;
    challenge.current = null;
    if (expiryTimer.current !== null) clearTimeout(expiryTimer.current);
    expiryTimer.current = null;
    busy.current = false;
    setPending(false);
    setReadyToVerify(false);
    setRevealed('');
  }, []);

  const clear = useCallback(() => {
    hide();
    setDraft('');
    setMessage('');
    setConfigured(false);
  }, [hide]);

  useEffect(() => {
    const generation = lifecycle.current.begin();
    // Defer transport setup so StrictMode cleanup never starts a doomed request.
    queueMicrotask(() => {
      if (!isOpen || !lifecycle.current.current(generation)) return;
      busy.current = true;
      setPending(true);
      void request(endpoint, lifecycle.current.signal)
        .then(parseCredentialStatus)
        .then((status) => {
          if (!lifecycle.current.current(generation)) return;
          setConfigured(status.configured);
          setMessage(
            status.configured
              ? 'OpenAI key configured.'
              : 'OpenAI key not configured.',
          );
        })
        .catch(() => {
          if (lifecycle.current.current(generation))
            setMessage(
              'OpenAI settings unavailable. Check your session and backend key.',
            );
        })
        .finally(() => {
          if (lifecycle.current.current(generation)) {
            busy.current = false;
            setPending(false);
          }
        });
    });
    return clear;
  }, [isOpen, uid, clear]);

  useEffect(() => {
    if (revealed) return scheduleRevealHide(hide);
  }, [revealed, hide]);

  const save = async () => {
    if (
      !isOpen ||
      busy.current ||
      !draft.trim() ||
      draft.length > 512 ||
      draft === SECRET_MASK
    )
      return;
    hide();
    const generation = lifecycle.current.begin();
    busy.current = true;
    setPending(true);
    setMessage('Saving OpenAI key…');
    try {
      const status = parseCredentialStatus(
        await request(endpoint, lifecycle.current.signal, { api_key: draft }),
      );
      if (!lifecycle.current.current(generation)) return;
      setDraft('');
      setConfigured(status.configured);
      setMessage(
        'Saved. OpenAI was not invoked. Explicit refresh and extraction use the configured key.',
      );
    } catch {
      if (lifecycle.current.current(generation))
        setMessage(
          'Save could not be confirmed. Reopen settings before retrying.',
        );
    } finally {
      if (lifecycle.current.current(generation)) {
        busy.current = false;
        setPending(false);
      }
    }
  };

  const prepareReveal = async () => {
    if (!isOpen || busy.current || !configured) return;
    hide();
    const generation = lifecycle.current.begin();
    busy.current = true;
    setPending(true);
    setMessage('Preparing verification. Next, select Verify with Google.');
    let isolated: StepUp | null = null;
    try {
      await runAuthOperation(async (operation) => {
        const sdk = await operation.wait(import('./firebase'));
        operation.check();
        isolated = sdk.createGoogleStepUp(uid);
        client.current = isolated;
        await operation.wait(isolated.ready());
        const prepared = parseRevealChallenge(
          await operation.wait(
            request(`${endpoint}/reveal/prepare`, operation.signal, {}),
          ),
        );
        if (!lifecycle.current.current(generation)) return;
        challenge.current = prepared;
        // Fixed server challenge lifetime; not a 15-second human popup deadline.
        expiryTimer.current = setTimeout(
          () => {
            if (lifecycle.current.current(generation)) {
              hide();
              setMessage('Verification expired. Select Reveal again.');
            }
          },
          Math.min(
            300_000,
            Math.max(0, prepared.expires_at * 1000 - Date.now()),
          ),
        );
        setReadyToVerify(true);
        setMessage(
          'Select Verify with Google and use this same administrator account.',
        );
      }, lifecycle.current.signal);
    } catch {
      // The generation owns its isolated client, never a newer panel's client.
      const failed = isolated as StepUp | null;
      failed?.dispose();
      if (lifecycle.current.current(generation)) {
        hide();
        setMessage(
          'Verification preparation failed. Select Reveal to try again.',
        );
      }
    } finally {
      if (lifecycle.current.current(generation)) {
        busy.current = false;
        setPending(false);
      }
    }
  };

  const verify = () => {
    const isolated = client.current;
    const prepared = challenge.current;
    if (!isOpen || busy.current || !readyToVerify || !isolated || !prepared)
      return;
    // Do not begin a new generation: it would cancel the prepared client's lifetime.
    const signal = lifecycle.current.signal;
    busy.current = true;
    setPending(true);
    setReadyToVerify(false);
    setMessage('Complete Google verification to reveal the key.');
    // Synchronous popup invocation preserves the user gesture. Only import/server
    // work is bounded to 15 seconds; human interaction uses challenge expiry.
    void waitForInteractiveVerification(isolated.verify(), signal)
      .then(async (token) => {
        signal.throwIfAborted();
        const value = parseRevealedKey(
          await request(`${endpoint}/reveal`, signal, {
            challenge: prepared.challenge,
            id_token: token,
          }),
        );
        signal.throwIfAborted();
        if (document.visibilityState === 'hidden')
          throw new Error('Reveal hidden.');
        if (expiryTimer.current !== null) clearTimeout(expiryTimer.current);
        expiryTimer.current = null;
        setRevealed(value);
        setMessage('Key visible for 30 seconds. Hide it when finished.');
      })
      .catch(() => {
        if (!signal.aborted)
          setMessage(
            'Key not revealed. Select Reveal and complete verification again.',
          );
      })
      .finally(() => {
        isolated.dispose();
        if (!signal.aborted) {
          busy.current = false;
          setPending(false);
          client.current = null;
          challenge.current = null;
        }
      });
  };

  return {
    draft,
    setDraft,
    revealed,
    configured,
    pending,
    message,
    readyToVerify,
    save,
    prepareReveal,
    verify,
    hide,
  };
}
