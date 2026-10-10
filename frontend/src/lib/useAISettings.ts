import { useCallback, useEffect, useRef, useState } from 'react';
import { AuthGeneration, runAuthOperation } from './authLifecycle';
import type { components } from './api.generated';

export type AIConfig = components['schemas']['AIConfig'];
export type AISettingsView = components['schemas']['AISettingsView'];
export type ModelOption = components['schemas']['ModelOption'];
export type ConnectionTest = components['schemas']['ConnectionTest'];
export type LoginStatus = components['schemas']['LoginStatus'];
export type AccessMode = AIConfig['access_mode'];

export const ACCESS_MODES: readonly {
  id: AccessMode;
  label: string;
  description: string;
}[] = [
  {
    id: 'openai_api_key',
    label: 'OpenAI API key',
    description: 'Paid per request; the key is managed under Credentials.',
  },
  {
    id: 'chatgpt_signin',
    label: 'Sign in with ChatGPT',
    description:
      'Uses a ChatGPT subscription with the OpenAI API. Officially aimed at personal or open-source local apps; hosted apps are waitlisted.',
  },
  {
    id: 'codex_device',
    label: 'Codex device login',
    description:
      'Unofficial ChatGPT Codex access through a device code; may break at any time.',
  },
  {
    id: 'custom',
    label: 'Custom server',
    description:
      'Any OpenAI-compatible server you run, reached over your network.',
  },
];

/** Standard pi-ai effort levels when a model does not publish its own. */
export const STANDARD_EFFORTS = ['off', 'minimal', 'low', 'medium', 'high'];
const base = '/api/admin/ai';

function call(
  path: string,
  signal: AbortSignal,
  method: 'GET' | 'PUT' | 'POST' | 'DELETE' = 'GET',
  body?: object,
): Promise<unknown> {
  return runAuthOperation(async (operation) => {
    const response = await operation.wait(
      fetch(base + path, {
        method,
        body: method === 'GET' ? undefined : JSON.stringify(body ?? {}),
        headers:
          method === 'GET'
            ? undefined
            : { 'Content-Type': 'application/json', 'X-WW-Request': '1' },
        credentials: 'same-origin',
        cache: 'no-store',
        signal: operation.signal,
      }),
    );
    if (!response.ok) {
      const detail = await operation
        .wait(response.json())
        .catch(() => null as unknown);
      const text =
        detail &&
        typeof detail === 'object' &&
        'detail' in detail &&
        typeof detail.detail === 'string' &&
        detail.detail.length <= 200
          ? detail.detail
          : 'AI settings unavailable.';
      throw new Error(text);
    }
    return operation.wait(response.json());
  }, signal);
}

function isView(value: unknown): value is AISettingsView {
  return (
    !!value &&
    typeof value === 'object' &&
    'draft' in value &&
    'credentials' in value &&
    typeof (value as AISettingsView).draft?.access_mode === 'string'
  );
}

function parseView(value: unknown): AISettingsView {
  if (!isView(value)) throw new Error('AI settings unavailable.');
  return value;
}

function parseLogin(value: unknown): LoginStatus | null {
  if (!value || typeof value !== 'object' || !('login' in value))
    throw new Error('Login status unavailable.');
  return (value as { login: LoginStatus | null }).login;
}

/**
 * Admin AI settings: one editable draft, explicit activation, and the
 * per-mode connect flows. Secrets typed here are sent once and never read back.
 */
export function useAISettings(isOpen: boolean, uid: string) {
  const lifecycle = useRef(new AuthGeneration());
  const [view, setView] = useState<AISettingsView | null>(null);
  const [draft, setDraftState] = useState<AIConfig | null>(null);
  const [dirty, setDirty] = useState(false);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [test, setTest] = useState<ConnectionTest | null>(null);
  const [login, setLogin] = useState<LoginStatus | null>(null);
  const [token, setToken] = useState('');
  const [callbackUrl, setCallbackUrl] = useState('');
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');

  const reset = useCallback(() => {
    lifecycle.current.begin();
    setView(null);
    setDraftState(null);
    setDirty(false);
    setModels([]);
    setTest(null);
    setLogin(null);
    setToken('');
    setCallbackUrl('');
    setPending(false);
    setMessage('');
  }, []);

  const run = useCallback(
    async <T>(
      work: (signal: AbortSignal) => Promise<T>,
      done?: string,
    ): Promise<T | undefined> => {
      const generation = lifecycle.current.begin();
      const signal = lifecycle.current.signal;
      setPending(true);
      setMessage('');
      try {
        const result = await work(signal);
        if (lifecycle.current.current(generation) && done) setMessage(done);
        return result;
      } catch (error) {
        if (lifecycle.current.current(generation))
          setMessage(
            error instanceof Error ? error.message : 'AI settings unavailable.',
          );
        return undefined;
      } finally {
        if (lifecycle.current.current(generation)) setPending(false);
      }
    },
    [],
  );

  const accept = useCallback((next: AISettingsView, keepDraft = false) => {
    setView(next);
    if (!keepDraft) {
      setDraftState(next.draft);
      setDirty(false);
    }
  }, []);

  useEffect(() => {
    const generations = lifecycle.current;
    let active = true;
    // Defer so StrictMode cleanup never starts a doomed request.
    queueMicrotask(() => {
      if (!active) return;
      reset();
      if (!isOpen) return;
      void run(async (signal) => {
        accept(parseView(await call('', signal)));
        setLogin(parseLogin(await call('/login', signal)));
      });
    });
    return () => {
      active = false;
      generations.begin();
    };
  }, [isOpen, uid, reset, run, accept]);

  // Codex device login: poll only while the server reports it pending.
  useEffect(() => {
    if (!isOpen || login?.kind !== 'codex_device' || login.state !== 'pending')
      return;
    const controller = new AbortController();
    const timer = setTimeout(
      () => {
        void call('/login', controller.signal)
          .then(async (value) => {
            const next = parseLogin(value);
            setLogin(next);
            if (next?.state === 'complete')
              accept(parseView(await call('', controller.signal)), true);
          })
          .catch(() => setMessage('Login status unavailable.'));
      },
      Math.max(2, login.interval_seconds ?? 5) * 1000,
    );
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [isOpen, login, accept]);

  const setDraft = useCallback((update: Partial<AIConfig>) => {
    setDraftState((current) => (current ? { ...current, ...update } : current));
    setDirty(true);
  }, []);

  const changeMode = useCallback(
    (mode: AccessMode) => {
      setModels([]);
      setTest(null);
      setDraft({
        access_mode: mode,
        model_id: null,
        reasoning_effort: null,
        custom_reasoning_field: null,
        custom_disable_with: null,
      });
    },
    [setDraft],
  );

  const save = useCallback(
    () =>
      run(async (signal) => {
        if (!draft) return;
        accept(parseView(await call('', signal, 'PUT', draft)));
      }, 'Settings saved. Extraction is unchanged until you activate them.'),
    [run, draft, accept],
  );

  const activate = useCallback(
    () =>
      run(async (signal) => {
        accept(
          parseView(await call('/activate', signal, 'POST', { confirm: true })),
          true,
        );
      }, 'Activated for extraction.'),
    [run, accept],
  );

  const deactivate = useCallback(
    () =>
      run(async (signal) => {
        accept(
          parseView(
            await call('/deactivate', signal, 'POST', { confirm: true }),
          ),
          true,
        );
      }, 'Extraction deactivated.'),
    [run, accept],
  );

  const loadModels = useCallback(
    () =>
      run(async (signal) => {
        if (!draft) return;
        const value =
          draft.access_mode === 'custom'
            ? await call('/custom/models', signal, 'POST')
            : await call(
                `/models?mode=${encodeURIComponent(draft.access_mode)}`,
                signal,
              );
        const list = (value as { models?: unknown }).models;
        if (!Array.isArray(list)) throw new Error('Models unavailable.');
        setModels(list as ModelOption[]);
      }, 'Models loaded.'),
    [run, draft],
  );

  const testCustom = useCallback(
    () =>
      run(async (signal) => {
        setTest((await call('/custom/test', signal, 'POST')) as ConnectionTest);
      }),
    [run],
  );

  const saveToken = useCallback(
    () =>
      run(async (signal) => {
        const value = token;
        setToken('');
        accept(
          parseView(
            await call('/custom/token', signal, 'PUT', { token: value }),
          ),
          true,
        );
      }, 'Server token saved.'),
    [run, token, accept],
  );

  const disconnect = useCallback(
    (slot: 'chatgpt_signin' | 'codex_device' | 'custom_token') =>
      run(async (signal) => {
        accept(
          parseView(await call(`/credentials/${slot}`, signal, 'DELETE')),
          true,
        );
      }, 'Disconnected.'),
    [run, accept],
  );

  const startChatGPT = useCallback(
    () =>
      run(async (signal) => {
        setCallbackUrl('');
        setLogin(parseLogin(await call('/chatgpt/start', signal, 'POST')));
      }),
    [run],
  );

  const completeChatGPT = useCallback(
    () =>
      run(async (signal) => {
        const pasted = callbackUrl;
        setCallbackUrl('');
        setLogin(
          parseLogin(
            await call('/chatgpt/complete', signal, 'POST', {
              callback_url: pasted,
            }),
          ),
        );
        accept(parseView(await call('', signal)), true);
      }, 'ChatGPT connected.'),
    [run, callbackUrl, accept],
  );

  const startCodex = useCallback(
    () =>
      run(async (signal) => {
        setLogin(parseLogin(await call('/codex/start', signal, 'POST')));
      }),
    [run],
  );

  const cancelLogin = useCallback(
    () =>
      run(async (signal) => {
        setCallbackUrl('');
        setLogin(parseLogin(await call('/login/cancel', signal, 'POST')));
      }),
    [run],
  );

  return {
    view,
    draft,
    dirty: dirty || token !== '' || callbackUrl !== '',
    models,
    test,
    login,
    token,
    setToken,
    callbackUrl,
    setCallbackUrl,
    pending,
    message,
    setDraft,
    changeMode,
    save,
    activate,
    deactivate,
    loadModels,
    testCustom,
    saveToken,
    disconnect,
    startChatGPT,
    completeChatGPT,
    startCodex,
    cancelLogin,
    clearDrafts: () => {
      setToken('');
      setCallbackUrl('');
    },
  };
}
