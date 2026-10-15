import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AuthGeneration,
  AuthHttpError,
  AuthQueue,
  classifySignInError,
  isTransientAuthFailure,
  parseRefusalCode,
  parseRetryAfter,
  parseSession,
  renewalRetryDelayMs,
  runAuthOperation,
  scheduleSessionRenewal,
  type AuthState,
} from './authLifecycle';

async function authRequest(path: string, body?: object, signal?: AbortSignal) {
  const response = await fetch(`/api/auth/${path}`, {
    signal: signal
      ? AbortSignal.any([signal, AbortSignal.timeout(15_000)])
      : AbortSignal.timeout(15_000),
    method: body ? 'POST' : 'GET',
    headers: body
      ? { 'Content-Type': 'application/json', 'X-WW-Request': '1' }
      : undefined,
    body: body ? JSON.stringify(body) : undefined,
    credentials: 'same-origin',
    cache: 'no-store',
  });
  if (!response.ok) {
    // Only an allowlisted refusal code is read from the body, never its text.
    let code = null;
    if (response.status === 403) {
      try {
        code = parseRefusalCode(await response.json());
      } catch {
        code = null;
      }
    }
    throw new AuthHttpError(
      path,
      response.status,
      parseRetryAfter(response.headers.get('Retry-After')),
      code,
    );
  }
  return response.json() as Promise<unknown>;
}

export function useAppAuth() {
  const [state, setState] = useState<AuthState>({ status: 'checking' });
  const generation = useRef(new AuthGeneration());
  const queue = useRef(new AuthQueue());
  const channel = useRef<BroadcastChannel | null>(null);

  const signOut = useCallback((broadcast = true, ended = false) => {
    const current = generation.current.begin();
    // Unmount readers/worker immediately.
    setState(
      ended
        ? { status: 'signed-out', notice: 'session-ended' }
        : { status: 'signed-out' },
    );
    if (broadcast) channel.current?.postMessage('signed-out');
    // Send invalidation immediately, even while a popup/token verification waits.
    // The database tombstone wins over a delayed exchange; queued new login waits.
    const invalidated = authRequest('logout', {});
    void invalidated.catch(() => {});
    // Keep invalidation ordered ahead of new prepare requests, but bound even
    // logout's SDK import/sign-out so it cannot hold the queue indefinitely.
    void queue.current
      .run(async (operation) => {
        await operation.wait(invalidated);
        if (!generation.current.current(current)) return;
        const firebase = await operation.wait(import('./firebase'));
        operation.check();
        if (generation.current.current(current))
          await operation.wait(firebase.signOutGoogle());
      })
      .catch(() => {
        if (generation.current.current(current)) setState({ status: 'error' });
      });
  }, []);

  useEffect(() => {
    const lifecycle = generation.current;
    const current = lifecycle.begin();
    let active = true;
    // Only an app-session read runs at startup; no workspace or Firebase request.
    void Promise.resolve().then(async () => {
      if (!active) return;
      try {
        const session = parseSession(await authRequest('session'));
        if (generation.current.current(current))
          setState({ status: 'ready', session });
      } catch {
        if (generation.current.current(current))
          setState({ status: 'signed-out' });
      }
    });
    if (typeof BroadcastChannel !== 'undefined') {
      channel.current = new BroadcastChannel('ww-app-auth');
      channel.current.onmessage = (event: MessageEvent<unknown>) => {
        if (event.data === 'signed-out') signOut(false);
      };
    }
    return () => {
      active = false;
      lifecycle.begin();
      channel.current?.close();
      channel.current = null;
    };
  }, [signOut]);

  const signIn = useCallback(() => {
    const current = generation.current.begin();
    setState({ status: 'checking' });
    void (async () => {
      try {
        // Bound module loading independently; never queue or time-limit the
        // user-interactive popup that follows it.
        const firebase = await runAuthOperation(
          (operation) => operation.wait(import('./firebase')),
          generation.current.signal,
        );
        if (!generation.current.current(current)) return;
        const tokenPromise = firebase.googleToken();
        const prepared = queue.current.run(async (operation) => {
          if (generation.current.current(current))
            await operation.wait(authRequest('prepare', {}, operation.signal));
        }, generation.current.signal);
        // A pending popup must not block logout or another tab's invalidation.
        const [, id_token] = await Promise.all([prepared, tokenPromise]);
        if (!generation.current.current(current)) return;
        await queue.current.run(async (operation) => {
          if (!generation.current.current(current)) return;
          const session = parseSession(
            await operation.wait(
              authRequest('exchange', { id_token }, operation.signal),
            ),
          );
          operation.check();
          if (generation.current.current(current))
            setState({ status: 'ready', session });
        }, generation.current.signal);
      } catch (error) {
        if (generation.current.current(current))
          setState({ status: 'error', reason: classifySignInError(error) });
      }
    })();
  }, []);

  useEffect(() => {
    if (state.status !== 'ready') return;
    const { session } = state;
    let active = true;
    const check = async () => {
      try {
        const actual = parseSession(await authRequest('session'));
        if (!active) return;
        if (actual.uid !== session.uid || actual.role !== session.role) {
          signOut(true, true);
          return;
        }
        // Admission changed (someone approved this account): continue the
        // same session with its new access; nothing is torn down.
        if (actual.approval !== session.approval)
          setState({ status: 'ready', session: actual });
      } catch (error) {
        // A rate limit or outage is not a verdict on the session; the next
        // poll or focus checks again, and expiry still ends it on time.
        if (active && !isTransientAuthFailure(error)) signOut(true, true);
      }
    };
    // A waiting account notices its approval sooner.
    const poll = window.setInterval(
      () => void check(),
      session.approval === 'pending' ? 10_000 : 30_000,
    );
    const focus = () => void check();
    window.addEventListener('focus', focus);
    return () => {
      active = false;
      window.clearInterval(poll);
      window.removeEventListener('focus', focus);
    };
  }, [state, signOut]);

  const session =
    state.status === 'ready' || state.status === 'refreshing'
      ? state.session
      : null;
  useEffect(() => {
    if (!session) return;
    const current = generation.current;
    let signal = current.signal;
    let retry: number | undefined;
    const renew = () => {
      if (signal.aborted) return;
      const epoch = current.begin();
      signal = current.signal;
      setState({ status: 'refreshing', session });
      void queue.current
        .run(async (operation) => {
          const firebase = await operation.wait(import('./firebase'));
          operation.check();
          const id_token = await operation.wait(
            firebase.renewedToken(session.uid),
          );
          operation.check();
          const next = parseSession(
            await operation.wait(
              authRequest('exchange', { id_token }, operation.signal),
            ),
          );
          operation.check();
          if (current.current(epoch))
            setState({ status: 'ready', session: next });
        }, current.signal)
        .catch((error: unknown) => {
          if (!current.current(epoch)) return;
          if (!isTransientAuthFailure(error)) {
            signOut(true, true);
            return;
          }
          // Keep the still-valid session and try again; the expiry timer
          // below still ends it if renewal never succeeds in time.
          setState({ status: 'ready', session });
          retry = window.setTimeout(renew, renewalRetryDelayMs(error));
        });
    };
    const cancel = scheduleSessionRenewal(session.expires_at, renew, () => {
      if (!signal.aborted) signOut(true, true);
    });
    return () => {
      cancel();
      window.clearTimeout(retry);
    };
  }, [session, signOut]);

  return { state, signIn, signOut: () => signOut() };
}
