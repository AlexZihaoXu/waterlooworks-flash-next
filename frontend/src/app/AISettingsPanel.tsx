import { useCallback, useState } from 'react';
import {
  Alert,
  AlertDialog,
  Button,
  Card,
  Chip,
  Description,
  Input,
  Label,
  Link,
  ListBox,
  NumberField,
  Radio,
  RadioGroup,
  Select,
  TextField,
} from '@heroui/react';
import { SECRET_MASK } from '../lib/openaiRevealState';
import { safeExternalUrl } from '../lib/safeExternalUrl';
import {
  ACCESS_MODES,
  STANDARD_EFFORTS,
  type AccessMode,
  type AIConfig,
  type ModelOption,
  type useAISettings,
} from '../lib/useAISettings';

type Settings = ReturnType<typeof useAISettings>;

function formatDate(value: string | null | undefined): string {
  if (!value) return 'unknown';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'unknown' : date.toLocaleString();
}

function modelLabel(model: ModelOption): string {
  const parts = [model.name];
  if (model.loaded === true) parts.push('loaded');
  if (model.loaded === false) parts.push('not loaded');
  if (model.context_window)
    parts.push(`${model.context_window.toLocaleString()} context`);
  return parts.join(' · ');
}

function NumberSetting({
  label,
  description,
  value,
  min,
  max,
  onChange,
  isDisabled,
}: {
  label: string;
  description: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
  isDisabled: boolean;
}) {
  return (
    <NumberField
      fullWidth
      minValue={min}
      maxValue={max}
      value={value}
      isDisabled={isDisabled}
      onChange={(next) => {
        if (typeof next === 'number' && Number.isInteger(next)) onChange(next);
      }}
    >
      <Label>{label}</Label>
      <NumberField.Group>
        <NumberField.DecrementButton />
        <NumberField.Input />
        <NumberField.IncrementButton />
      </NumberField.Group>
      <Description>{description}</Description>
    </NumberField>
  );
}

// Documented HeroUI v3 composition only (Card, RadioGroup, Select,
// NumberField, TextField, AlertDialog); native divs only lay out the grid.
export function AISettingsPanel({
  ai,
  onManageApiKey,
}: {
  ai: Settings;
  onManageApiKey: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [portal, setPortal] = useState<HTMLElement | null>(null);
  const anchor = useCallback((node: HTMLDivElement | null) => {
    // Portal overlays into the native modal: <body> sits beneath its top layer.
    setPortal(node?.closest('dialog') ?? null);
  }, []);
  const { draft, view } = ai;
  if (!draft || !view)
    return (
      <div ref={anchor}>
        <p role="status">{ai.message || 'Loading AI settings…'}</p>
      </div>
    );
  const credentials = view.credentials;
  const mode = draft.access_mode;
  const set = (update: Partial<AIConfig>) => ai.setDraft(update);
  const options: ModelOption[] =
    draft.model_id && !ai.models.some((model) => model.id === draft.model_id)
      ? [
          ...ai.models,
          { id: draft.model_id, name: draft.model_id, efforts: [] },
        ]
      : ai.models;
  const selected = options.find((model) => model.id === draft.model_id);
  const efforts =
    selected && selected.efforts.length ? selected.efforts : STANDARD_EFFORTS;
  const login = ai.login?.kind === mode ? ai.login : null;
  const authorizeUrl = safeExternalUrl(login?.authorize_url);
  const verificationUrl = safeExternalUrl(login?.verification_uri);
  const connected =
    mode === 'custom' ||
    (credentials[mode === 'openai_api_key' ? 'openai_api_key' : mode]
      ?.configured ??
      false);

  return (
    <div ref={anchor} className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <span role="status">
          <Chip color={view.active ? 'success' : 'default'} size="sm">
            {view.active_label
              ? `Active: ${view.active_label}`
              : 'Extraction not activated'}
          </Chip>
        </span>
        {view.activated_at && (
          <Description>Activated {formatDate(view.activated_at)}</Description>
        )}
      </div>
      <RadioGroup
        value={mode}
        onChange={(value) => ai.changeMode(value as AccessMode)}
        isDisabled={ai.pending}
      >
        <Label>Access mode</Label>
        <Description>
          Saving these settings never calls a model or changes extraction until
          you activate them.
        </Description>
        {ACCESS_MODES.map((option) => (
          <Radio key={option.id} value={option.id}>
            <Radio.Content>
              <Radio.Control>
                <Radio.Indicator />
              </Radio.Control>
              {option.label}
            </Radio.Content>
            <Description>{option.description}</Description>
          </Radio>
        ))}
      </RadioGroup>

      <Card variant="secondary">
        <Card.Header>
          <Card.Title>
            {ACCESS_MODES.find((option) => option.id === mode)?.label}
          </Card.Title>
          <Card.Description>
            {mode === 'custom'
              ? 'Requests go only to this validated address; link-local, metadata and this app’s own network are refused.'
              : connected
                ? 'Connected. Tokens are stored encrypted and never shown.'
                : 'Not connected.'}
          </Card.Description>
        </Card.Header>
        <Card.Content>
          <div className="grid gap-4">
            {mode === 'openai_api_key' && (
              <>
                <p>
                  API key:{' '}
                  {credentials.openai_api_key?.configured
                    ? 'configured'
                    : 'not configured'}
                </p>
                <div>
                  <Button variant="secondary" onPress={onManageApiKey}>
                    Manage key in Credentials
                  </Button>
                </div>
                <Description>
                  Paid per request. The USD stops in Usage apply to this mode.
                </Description>
              </>
            )}
            {mode === 'chatgpt_signin' && (
              <>
                <Alert>
                  <Alert.Indicator />
                  <Alert.Content>
                    <Alert.Title>Manual sign-in</Alert.Title>
                    <Alert.Description>
                      Sign in with ChatGPT targets personal or open-source local
                      apps; remotely hosted apps are waitlisted. After signing
                      in, your browser opens an unreachable 127.0.0.1:1455
                      address: copy that full address and paste it here.
                    </Alert.Description>
                  </Alert.Content>
                </Alert>
                {credentials.chatgpt_signin?.configured && (
                  <p>
                    Connected; token expires{' '}
                    {formatDate(credentials.chatgpt_signin.expires_at)} and
                    refreshes automatically.
                  </p>
                )}
                {login?.state === 'pending' && authorizeUrl ? (
                  <>
                    <Link
                      href={authorizeUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Open ChatGPT sign-in
                      <Link.Icon />
                    </Link>
                    <TextField
                      fullWidth
                      value={ai.callbackUrl}
                      onChange={ai.setCallbackUrl}
                      isDisabled={ai.pending}
                    >
                      <Label>Pasted callback address</Label>
                      <Input
                        autoComplete="off"
                        spellCheck={false}
                        autoCapitalize="none"
                        placeholder="http://127.0.0.1:1455/auth/callback?…"
                      />
                      <Description>
                        Expires{' '}
                        {formatDate(
                          new Date(login.expires_at * 1000).toISOString(),
                        )}
                        .
                      </Description>
                    </TextField>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        onPress={() => void ai.completeChatGPT()}
                        isDisabled={ai.pending || !ai.callbackUrl.trim()}
                        isPending={ai.pending}
                      >
                        Finish sign-in
                      </Button>
                      <Button
                        variant="tertiary"
                        onPress={() => void ai.cancelLogin()}
                      >
                        Cancel
                      </Button>
                    </div>
                  </>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="secondary"
                      onPress={() => void ai.startChatGPT()}
                      isDisabled={ai.pending}
                    >
                      {credentials.chatgpt_signin?.configured
                        ? 'Reconnect ChatGPT'
                        : 'Connect ChatGPT'}
                    </Button>
                    {credentials.chatgpt_signin?.configured && (
                      <Button
                        variant="danger-soft"
                        onPress={() => void ai.disconnect('chatgpt_signin')}
                        isDisabled={ai.pending}
                      >
                        Disconnect
                      </Button>
                    )}
                  </div>
                )}
                {login && login.state !== 'pending' && (
                  <p>
                    Last sign-in: {login.state}
                    {login.error_category ? ` (${login.error_category})` : ''}
                  </p>
                )}
              </>
            )}
            {mode === 'codex_device' && (
              <>
                <Alert status="warning">
                  <Alert.Indicator />
                  <Alert.Content>
                    <Alert.Title>Unofficial; may break</Alert.Title>
                    <Alert.Description>
                      Codex device login is not an official API for third-party
                      apps and can stop working without notice.
                    </Alert.Description>
                  </Alert.Content>
                </Alert>
                {credentials.codex_device?.configured && (
                  <p>
                    Connected; token expires{' '}
                    {formatDate(credentials.codex_device.expires_at)} and
                    refreshes automatically.
                  </p>
                )}
                {login?.state === 'pending' && login.user_code ? (
                  <>
                    <TextField isReadOnly value={login.user_code}>
                      <Label>Device code</Label>
                      <Input autoComplete="off" spellCheck={false} />
                      <Description>
                        Enter this code at the link below; waiting for approval…
                      </Description>
                    </TextField>
                    {verificationUrl && (
                      <Link
                        href={verificationUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Open device sign-in
                        <Link.Icon />
                      </Link>
                    )}
                    <div>
                      <Button
                        variant="tertiary"
                        onPress={() => void ai.cancelLogin()}
                      >
                        Cancel
                      </Button>
                    </div>
                  </>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="secondary"
                      onPress={() => void ai.startCodex()}
                      isDisabled={ai.pending}
                    >
                      {credentials.codex_device?.configured
                        ? 'Reconnect Codex'
                        : 'Connect Codex'}
                    </Button>
                    {credentials.codex_device?.configured && (
                      <Button
                        variant="danger-soft"
                        onPress={() => void ai.disconnect('codex_device')}
                        isDisabled={ai.pending}
                      >
                        Disconnect
                      </Button>
                    )}
                  </div>
                )}
                {login && login.state !== 'pending' && (
                  <p>
                    Last device login: {login.state}
                    {login.error_category ? ` (${login.error_category})` : ''}
                  </p>
                )}
              </>
            )}
            {mode === 'custom' && (
              <>
                <TextField
                  fullWidth
                  value={draft.custom_base_url ?? ''}
                  onChange={(value) =>
                    set({ custom_base_url: value.trim() ? value : null })
                  }
                  isDisabled={ai.pending}
                >
                  <Label>Base URL</Label>
                  <Input
                    autoComplete="off"
                    spellCheck={false}
                    autoCapitalize="none"
                    placeholder="http://100.64.0.10:8000/v1"
                  />
                  <Description>
                    Save settings before testing; checks use the saved address.
                  </Description>
                </TextField>
                <TextField
                  fullWidth
                  value={ai.token}
                  onChange={ai.setToken}
                  isDisabled={ai.pending}
                >
                  <Label>Replacement server token (optional)</Label>
                  <Input
                    type="password"
                    placeholder={
                      credentials.custom_token?.configured
                        ? SECRET_MASK
                        : undefined
                    }
                    autoComplete="new-password"
                    spellCheck={false}
                    autoCapitalize="none"
                  />
                </TextField>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="secondary"
                    onPress={() => void ai.saveToken()}
                    isDisabled={
                      ai.pending || !ai.token.trim() || ai.token === SECRET_MASK
                    }
                  >
                    Save token
                  </Button>
                  {credentials.custom_token?.configured && (
                    <Button
                      variant="danger-soft"
                      onPress={() => void ai.disconnect('custom_token')}
                      isDisabled={ai.pending}
                    >
                      Remove token
                    </Button>
                  )}
                  <Button
                    variant="secondary"
                    onPress={() => void ai.testCustom()}
                    isDisabled={ai.pending || ai.dirty}
                  >
                    Test connection
                  </Button>
                </div>
                {ai.test && (
                  <p role="status">
                    {ai.test.error_category === null
                      ? `Reachable · HTTP ${ai.test.status_code} · ${ai.test.latency_ms} ms · ${ai.test.model_count} models`
                      : `Not usable: ${ai.test.error_category}${ai.test.status_code ? ` (HTTP ${ai.test.status_code})` : ''}`}
                  </p>
                )}
                <NumberSetting
                  label="Context window (tokens)"
                  description="Filled from the server when it reports one; otherwise enter it."
                  value={draft.custom_context_window ?? 32768}
                  min={1024}
                  max={10_000_000}
                  isDisabled={ai.pending}
                  onChange={(value) => set({ custom_context_window: value })}
                />
              </>
            )}
          </div>
        </Card.Content>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="grid content-start gap-2">
          <Select
            fullWidth
            placeholder="Load models first"
            value={draft.model_id ?? null}
            isDisabled={ai.pending || options.length === 0}
            onChange={(key) => {
              const model = options.find((item) => item.id === key);
              if (!model) return;
              set({
                model_id: model.id,
                reasoning_effort: model.default_effort ?? null,
                ...(mode === 'custom'
                  ? {
                      custom_context_window:
                        model.context_window ??
                        draft.custom_context_window ??
                        null,
                      custom_reasoning_field: model.reasoning_field ?? null,
                      custom_disable_with: model.disable_with ?? null,
                    }
                  : {}),
              });
            }}
          >
            <Label>Model</Label>
            <Select.Trigger>
              <Select.Value />
              <Select.Indicator />
            </Select.Trigger>
            <Select.Popover UNSTABLE_portalContainer={portal ?? undefined}>
              <ListBox>
                {options.map((model) => (
                  <ListBox.Item
                    key={model.id}
                    id={model.id}
                    textValue={modelLabel(model)}
                  >
                    {modelLabel(model)}
                    <ListBox.ItemIndicator />
                  </ListBox.Item>
                ))}
              </ListBox>
            </Select.Popover>
          </Select>
          <div>
            <Button
              variant="secondary"
              onPress={() => void ai.loadModels()}
              isDisabled={
                ai.pending || (mode === 'custom' && ai.dirty) || !connected
              }
            >
              Load models
            </Button>
          </div>
        </div>
        <Select
          fullWidth
          placeholder="Provider default"
          value={draft.reasoning_effort ?? null}
          isDisabled={ai.pending || !draft.model_id}
          onChange={(key) =>
            set({ reasoning_effort: typeof key === 'string' ? key : null })
          }
        >
          <Label>Reasoning effort</Label>
          <Select.Trigger>
            <Select.Value />
            <Select.Indicator />
          </Select.Trigger>
          <Select.Popover UNSTABLE_portalContainer={portal ?? undefined}>
            <ListBox>
              {efforts.map((effort) => (
                <ListBox.Item key={effort} id={effort} textValue={effort}>
                  {effort === 'off' ? 'Off' : effort}
                  <ListBox.ItemIndicator />
                </ListBox.Item>
              ))}
            </ListBox>
          </Select.Popover>
          <Description>
            Options come from the model when it reports them.
          </Description>
        </Select>
        <NumberSetting
          label="Parallel streams"
          description="Simultaneous postings; custom servers usually serve one."
          value={draft.parallel_streams}
          min={1}
          max={8}
          isDisabled={ai.pending}
          onChange={(value) => set({ parallel_streams: value })}
        />
        <NumberSetting
          label="Postings per run"
          description="Postings beyond this wait for the next refresh."
          value={draft.postings_per_run}
          min={1}
          max={10_000}
          isDisabled={ai.pending}
          onChange={(value) => set({ postings_per_run: value })}
        />
        <NumberSetting
          label="Max turns per posting"
          description="Model requests per posting before it fails."
          value={draft.max_turns}
          min={1}
          max={24}
          isDisabled={ai.pending}
          onChange={(value) => set({ max_turns: value })}
        />
        <NumberSetting
          label="Max tool calls per posting"
          description="Section reads, searches and pay checks combined."
          value={draft.max_tool_calls}
          min={1}
          max={48}
          isDisabled={ai.pending}
          onChange={(value) => set({ max_tool_calls: value })}
        />
        <NumberSetting
          label="Max output tokens"
          description="Per model request, including reasoning."
          value={draft.max_output_tokens}
          min={256}
          max={65_536}
          isDisabled={ai.pending}
          onChange={(value) => set({ max_output_tokens: value })}
        />
        <NumberSetting
          label="Request timeout (seconds)"
          description="Per model request."
          value={draft.request_timeout_seconds}
          min={10}
          max={600}
          isDisabled={ai.pending}
          onChange={(value) => set({ request_timeout_seconds: value })}
        />
        {mode !== 'openai_api_key' && (
          <NumberSetting
            label="Requests per run"
            description="Subscription and custom modes record $0 and are bounded by request count."
            value={draft.request_limit_per_run}
            min={1}
            max={10_000}
            isDisabled={ai.pending}
            onChange={(value) => set({ request_limit_per_run: value })}
          />
        )}
      </div>

      <p role="status">{ai.message}</p>
      <div className="flex flex-wrap gap-2">
        <Button
          onPress={() => void ai.save()}
          isDisabled={ai.pending || !ai.dirty}
          isPending={ai.pending}
        >
          Save AI settings
        </Button>
        <Button
          variant="secondary"
          onPress={() => setConfirming(true)}
          isDisabled={
            ai.pending || ai.dirty || !view.draft.model_id || !connected
          }
        >
          Use for extraction…
        </Button>
        {view.active && (
          <Button
            variant="tertiary"
            onPress={() => void ai.deactivate()}
            isDisabled={ai.pending}
          >
            Deactivate
          </Button>
        )}
      </div>
      {portal && (
        <AlertDialog.Backdrop
          isOpen={confirming}
          onOpenChange={setConfirming}
          UNSTABLE_portalContainer={portal}
        >
          <AlertDialog.Container size="sm">
            <AlertDialog.Dialog>
              <AlertDialog.Header>
                <AlertDialog.Icon status="warning" />
                <AlertDialog.Heading>
                  Use these AI settings?
                </AlertDialog.Heading>
              </AlertDialog.Header>
              <AlertDialog.Body>
                <p>
                  The next Refresh will extract with{' '}
                  {
                    ACCESS_MODES.find((o) => o.id === view.draft.access_mode)
                      ?.label
                  }{' '}
                  / {view.draft.model_id}. Earlier results stay readable.
                  {view.draft.access_mode === 'openai_api_key'
                    ? ' API-key requests are paid.'
                    : ''}
                </p>
              </AlertDialog.Body>
              <AlertDialog.Footer>
                <Button slot="close" variant="tertiary">
                  Cancel
                </Button>
                <Button
                  onPress={() => {
                    setConfirming(false);
                    void ai.activate();
                  }}
                >
                  Activate
                </Button>
              </AlertDialog.Footer>
            </AlertDialog.Dialog>
          </AlertDialog.Container>
        </AlertDialog.Backdrop>
      )}
    </div>
  );
}
