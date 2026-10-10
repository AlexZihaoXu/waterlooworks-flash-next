import {
  useCallback,
  useImperativeHandle,
  useRef,
  useState,
  type ReactNode,
  type Ref,
} from 'react';
import { FcGoogle } from 'react-icons/fc';
import { LuUserRound } from 'react-icons/lu';
import {
  Accordion,
  Alert,
  Avatar,
  Button,
  Card,
  Chip,
  Description,
  Dropdown,
  Form,
  Input,
  Label,
  Meter,
  Skeleton,
  Tabs,
  TextField,
  Typography,
} from '@heroui/react';
import { useAdminCredentials } from '../lib/useAdminCredentials';
import { useOpenAISettings } from '../lib/useOpenAISettings';
import {
  useAdminRefreshStatus,
  type RefreshPacing,
} from '../lib/useAdminRefreshStatus';
import { refreshStopLabel } from '../lib/refreshProgress';
import { SECRET_MASK } from '../lib/openaiRevealState';
import { googlePhotoUrl, type AccountProfile } from '../lib/accountProfile';
import { useAccountProfile } from '../lib/useAccountProfile';
import type { AuthState } from '../lib/authLifecycle';
import { useAppAuth } from '../lib/useAppAuth';
import { App } from './App';
import { AdminSettingsDialog } from './AdminSettingsDialog';

function localTime(value: string | null): string | null {
  return value === null ? null : new Date(value).toLocaleString();
}

// Documented HeroUI Card/Meter/Alert composition; read-only pacing status.
export function RefreshPacingCard({
  pacing,
}: {
  pacing: RefreshPacing | null;
}) {
  if (!pacing)
    return (
      <Card variant="secondary">
        <Card.Header>
          <Card.Title>WaterlooWorks pacing</Card.Title>
          <Card.Description>Pacing status is loading.</Card.Description>
        </Card.Header>
      </Card>
    );
  const blocked = localTime(pacing.blocked_until);
  const next = localTime(pacing.next_allowed_at);
  const stop = refreshStopLabel(pacing.last_pacing_stop);
  return (
    <Card variant="secondary">
      <Card.Header>
        <Card.Title>WaterlooWorks pacing</Card.Title>
        <Card.Description>
          Every refresh request waits {pacing.min_gap_seconds}–
          {pacing.max_gap_seconds} s after the previous one, rests{' '}
          {pacing.rest_min_seconds}–{pacing.rest_max_seconds} s every{' '}
          {pacing.rest_every_min}–{pacing.rest_every_max} requests, and never
          runs in parallel. Refreshes are manual only.
        </Card.Description>
      </Card.Header>
      <Card.Content>
        <div className="grid gap-3">
          {pacing.last_pacing_stop === 'ban_risk' && (
            <Alert status="danger">
              <Alert.Indicator />
              <Alert.Content>
                <Alert.Title>Ban-risk warning from WaterlooWorks</Alert.Title>
                <Alert.Description>
                  The last refresh stopped at once (
                  {pacing.last_error_category ?? 'unknown'}). Check the account
                  in a browser before the operator clears the block.
                </Alert.Description>
              </Alert.Content>
            </Alert>
          )}
          {blocked && (
            <Alert status="warning">
              <Alert.Indicator />
              <Alert.Content>
                <Alert.Title>Refresh blocked until {blocked}</Alert.Title>
                <Alert.Description>
                  Set after a rate-limit or sign-in warning. Only the operator
                  rate_limit clear command removes it early.
                </Alert.Description>
              </Alert.Content>
            </Alert>
          )}
          {stop && pacing.last_pacing_stop !== 'ban_risk' && (
            <Alert status="accent">
              <Alert.Indicator />
              <Alert.Content>
                <Alert.Title>{stop}</Alert.Title>
                <Alert.Description>
                  Saved progress is kept; the next refresh continues with
                  postings not yet fetched.
                </Alert.Description>
              </Alert.Content>
            </Alert>
          )}
          <Meter
            value={pacing.used_24h}
            maxValue={pacing.daily_request_cap}
            color={pacing.remaining_24h === 0 ? 'danger' : 'accent'}
            valueLabel={`${pacing.used_24h} / ${pacing.daily_request_cap}`}
          >
            <Label>WaterlooWorks requests in the last 24 hours</Label>
            <Meter.Output />
            <Meter.Track>
              <Meter.Fill />
            </Meter.Track>
          </Meter>
          <p>
            {pacing.remaining_24h} requests remaining · {pacing.request_cap} per
            refresh · {pacing.min_interval_minutes} min between refreshes
          </p>
          <p role="status">
            {next ? `Next refresh allowed at ${next}.` : 'Refresh allowed now.'}
          </p>
        </div>
      </Card.Content>
    </Card>
  );
}

// Documented HeroUI Card/Button/Alert composition; no custom visual widget.
export function AuthGate({
  state,
  signIn,
  children,
}: {
  state: AuthState;
  signIn: () => void;
  children: ReactNode;
}) {
  // Renewal keeps the same verified session: never tear down the workspace.
  if (state.status === 'ready' || state.status === 'refreshing')
    return children;
  const pending = state.status === 'checking';
  return (
    <App
      key="signed-out"
      state={{ status: 'signed-out' }}
      signedOutContent={
        <Card>
          <Card.Header>
            <Card.Title>Sign in to continue</Card.Title>
            <Card.Description>
              Sign in with Google to browse shared saved jobs. Only the
              administrator can refresh them.
            </Card.Description>
          </Card.Header>
          <Card.Content>
            {state.status === 'error' && state.reason === 'cancelled' && (
              <Alert>
                <Alert.Indicator />
                <Alert.Content>
                  <Alert.Title>Sign-in cancelled</Alert.Title>
                  <Alert.Description>
                    The Google window closed before sign-in finished. Try again
                    when you are ready.
                  </Alert.Description>
                </Alert.Content>
              </Alert>
            )}
            {state.status === 'error' && state.reason === 'rejected' && (
              <Alert status="warning">
                <Alert.Indicator />
                <Alert.Content>
                  <Alert.Title>Sign-in not accepted</Alert.Title>
                  <Alert.Description>
                    Google sign-in was not accepted, possibly because it took
                    too long. Try again, or use a different Google account.
                  </Alert.Description>
                </Alert.Content>
              </Alert>
            )}
            {state.status === 'error' && state.reason === 'owner-required' && (
              <Alert status="warning">
                <Alert.Indicator />
                <Alert.Content>
                  <Alert.Title>Owner sign-in required</Alert.Title>
                  <Alert.Description>
                    This instance has no administrator yet, and only its
                    configured owner can sign in first. Use the owner's Google
                    account.
                  </Alert.Description>
                </Alert.Content>
              </Alert>
            )}
            {state.status === 'error' &&
              (state.reason === undefined ||
                state.reason === 'unavailable') && (
                <Alert status="danger">
                  <Alert.Indicator />
                  <Alert.Content>
                    <Alert.Title>Authentication unavailable</Alert.Title>
                    <Alert.Description>
                      Sign-in or sign-out could not be confirmed. Check your
                      connection (and that pop-ups are allowed), then try again.
                      Saved jobs are hidden.
                    </Alert.Description>
                  </Alert.Content>
                </Alert>
              )}
            {state.status === 'signed-out' &&
              state.notice === 'session-ended' && (
                <Alert status="warning">
                  <Alert.Indicator />
                  <Alert.Content>
                    <Alert.Title>Session ended</Alert.Title>
                    <Alert.Description>
                      Your session could not be renewed or confirmed. Sign in
                      again to continue. Saved jobs are hidden until then.
                    </Alert.Description>
                  </Alert.Content>
                </Alert>
              )}
          </Card.Content>
          <Card.Footer>
            <Button
              variant="secondary"
              onPress={signIn}
              isPending={pending}
              isDisabled={pending}
              fullWidth
            >
              <FcGoogle aria-hidden="true" />
              Sign in with Google
            </Button>
          </Card.Footer>
        </Card>
      }
    />
  );
}

// Documented HeroUI composition; native spans only lay out the account text.
export function AccountMenu({
  profile,
  signOut,
  role = 'member',
  uid = '',
  settingsRef,
}: {
  profile: AccountProfile | null;
  signOut: () => void;
  role?: 'admin' | 'member';
  uid?: string;
  /** Admin only: lets workspace notices open Admin settings. */
  settingsRef?: Ref<AdminSettingsHandle>;
}) {
  const photo = googlePhotoUrl(profile?.photo_url);
  const content = (
    <>
      <Avatar size="sm">
        {photo && (
          <Avatar.Image src={photo} alt="" referrerPolicy="no-referrer" />
        )}
        <Avatar.Fallback>
          <LuUserRound aria-hidden="true" />
        </Avatar.Fallback>
      </Avatar>
      <span className="account-text">
        <span className="account-name">
          {profile?.display_name || 'Account'}
        </span>
        {profile?.email && (
          <span className="account-email">{profile.email}</span>
        )}
      </span>
    </>
  );
  if (role === 'admin')
    return (
      <AdminAccountMenu
        key={uid}
        uid={uid}
        content={content}
        signOut={signOut}
        settingsRef={settingsRef}
      />
    );
  return (
    <Dropdown>
      <Button aria-label="Account menu" variant="ghost" size="lg">
        {content}
      </Button>
      <Dropdown.Popover placement="bottom end">
        <Dropdown.Menu
          aria-label="Account actions"
          onAction={(key) => {
            if (key === 'sign-out') signOut();
          }}
        >
          <Dropdown.Item id="sign-out" textValue="Sign out">
            <Label>Sign out</Label>
          </Dropdown.Item>
        </Dropdown.Menu>
      </Dropdown.Popover>
    </Dropdown>
  );
}

export type AdminSettingsHandle = { open: () => void };

// Official v3 content in the owner-approved bounded native dialog shell.
function AdminAccountMenu({
  uid,
  content,
  signOut,
  settingsRef,
}: {
  uid: string;
  content: ReactNode;
  signOut: () => void;
  settingsRef?: Ref<AdminSettingsHandle>;
}) {
  const [selectedTab, setSelectedTab] = useState('credentials');
  const settings = useAdminCredentials(uid);
  const openai = useOpenAISettings(
    settings.isOpen && selectedTab === 'credentials',
    uid,
  );
  const refresh = useAdminRefreshStatus(settings.isOpen, uid);
  // Workspace Duo notice: open straight to the Refresh tab. Never reopen an
  // open dialog, because opening clears and rereads credential state.
  useImperativeHandle(settingsRef, () => ({
    open: () => {
      if (settings.isOpen) return;
      setSelectedTab('refresh');
      settings.setOpen(true);
    },
  }));
  const closeSettings = (open: boolean) => {
    refresh.clear();
    openai.hide();
    openai.setDraft('');
    settings.setOpen(open);
    if (open) setSelectedTab('credentials');
  };
  const selectTab = (key: string) => {
    if (selectedTab === 'credentials' && key !== 'credentials') {
      // Both Tabs and the Duo shortcut must synchronously invalidate reveal.
      // Cancelling a save is not rollback; entry rereads current metadata.
      openai.hide();
      openai.setDraft('');
    }
    setSelectedTab(key);
  };
  return (
    <>
      <Button
        aria-label="Account menu"
        variant="ghost"
        size="lg"
        onPress={() => {
          setSelectedTab('credentials');
          settings.setOpen(true);
        }}
      >
        {content}
      </Button>
      {settings.isOpen && (
        <AdminSettingsDialog
          onDismiss={() => closeSettings(false)}
          dirty={
            settings.username !== '' ||
            settings.password !== '' ||
            openai.draft !== ''
          }
          notification={
            refresh.challenge?.approval_required && (
              <div className="flex flex-wrap items-center gap-4">
                <span role="status">
                  <Chip color="warning" size="sm">
                    Duo approval required
                  </Chip>
                </span>
                {selectedTab === 'refresh' ? (
                  <Description>
                    Approve only a refresh you requested.
                  </Description>
                ) : (
                  <Button
                    size="sm"
                    variant="secondary"
                    onPress={() => selectTab('refresh')}
                  >
                    Go to Refresh
                  </Button>
                )}
              </div>
            )
          }
          footer={(requestDismiss) => (
            <>
              {/* Explicit sign-out always proceeds and clears drafts. */}
              <Button
                variant="ghost"
                onPress={() => {
                  closeSettings(false);
                  signOut();
                }}
              >
                Sign out
              </Button>
              <Button variant="secondary" onPress={requestDismiss}>
                Done
              </Button>
            </>
          )}
        >
          <Tabs
            selectedKey={selectedTab}
            onSelectionChange={(key) => selectTab(String(key))}
          >
            <Tabs.ListContainer>
              <Tabs.List aria-label="Admin settings sections">
                <Tabs.Tab id="credentials">
                  Credentials
                  <Tabs.Indicator />
                </Tabs.Tab>
                <Tabs.Tab id="usage">
                  Usage
                  <Tabs.Indicator />
                </Tabs.Tab>
                <Tabs.Tab id="refresh">
                  Refresh
                  <Tabs.Indicator />
                </Tabs.Tab>
              </Tabs.List>
            </Tabs.ListContainer>
            <Tabs.Panel id="credentials">
              <div className="grid gap-4 sm:grid-cols-2">
                <Card variant="secondary">
                  <Card.Header>
                    <Card.Title>WaterlooWorks</Card.Title>
                    <Card.Description>
                      Enter both fields to replace the shared login. Saving does
                      not sign in or collect jobs.
                    </Card.Description>
                  </Card.Header>
                  <Card.Content>
                    <div className="grid gap-4">
                      {settings.savedUsername !== null && (
                        <p className="break-all">
                          Saved username/email: {settings.savedUsername}
                        </p>
                      )}
                      <Form
                        aria-label="WaterlooWorks credentials"
                        autoComplete="off"
                        onSubmit={(event) => {
                          event.preventDefault();
                          void settings.save();
                        }}
                      >
                        <div className="grid gap-4">
                          <TextField
                            isRequired
                            fullWidth
                            value={settings.username}
                            onChange={settings.setUsername}
                            isDisabled={settings.pending}
                          >
                            <Label>WaterlooWorks username</Label>
                            <Input
                              autoComplete="off"
                              maxLength={256}
                              spellCheck={false}
                              autoCapitalize="none"
                            />
                          </TextField>
                          <TextField
                            isRequired
                            fullWidth
                            value={settings.password}
                            onChange={settings.setPassword}
                            isDisabled={settings.pending}
                          >
                            <Label>WaterlooWorks password</Label>
                            <Input
                              type="password"
                              placeholder={
                                settings.configured ? SECRET_MASK : undefined
                              }
                              autoComplete="new-password"
                              maxLength={1024}
                              spellCheck={false}
                            />
                            <Description>
                              The saved password cannot be revealed.
                            </Description>
                          </TextField>
                          <p role="status">{settings.message}</p>
                          <div>
                            <Button
                              type="submit"
                              isPending={settings.pending}
                              isDisabled={
                                settings.pending ||
                                !settings.username.trim() ||
                                !settings.password ||
                                settings.password === SECRET_MASK
                              }
                            >
                              Save credentials
                            </Button>
                          </div>
                        </div>
                      </Form>
                    </div>
                  </Card.Content>
                </Card>
                <Card variant="secondary">
                  <Card.Header>
                    <Card.Title>OpenAI</Card.Title>
                    <Card.Description>
                      Used by automatic extraction after Refresh. Saving never
                      invokes OpenAI.
                    </Card.Description>
                  </Card.Header>
                  <Card.Content>
                    <div className="grid gap-4">
                      <Form
                        aria-label="OpenAI key"
                        autoComplete="off"
                        onSubmit={(event) => {
                          event.preventDefault();
                          void openai.save();
                        }}
                      >
                        <div className="grid gap-4">
                          <TextField
                            fullWidth
                            isRequired
                            value={openai.draft}
                            onChange={openai.setDraft}
                            isDisabled={openai.pending}
                          >
                            <Label>Replacement OpenAI API key</Label>
                            <Input
                              type="password"
                              placeholder={
                                openai.configured ? SECRET_MASK : undefined
                              }
                              maxLength={512}
                              autoComplete="new-password"
                              spellCheck={false}
                              autoCapitalize="none"
                            />
                          </TextField>
                          <div>
                            <Button
                              type="submit"
                              isPending={openai.pending}
                              isDisabled={
                                openai.pending ||
                                !openai.draft.trim() ||
                                openai.draft === SECRET_MASK
                              }
                            >
                              Save OpenAI key
                            </Button>
                          </div>
                        </div>
                      </Form>
                      <p role="status">{openai.message}</p>
                      <div className="grid justify-items-start gap-4">
                        {openai.revealed ? (
                          <>
                            <TextField
                              fullWidth
                              isReadOnly
                              value={openai.revealed}
                            >
                              <Label>Revealed OpenAI API key</Label>
                              <Input autoComplete="off" spellCheck={false} />
                            </TextField>
                            <Button variant="secondary" onPress={openai.hide}>
                              Hide
                            </Button>
                          </>
                        ) : openai.readyToVerify ? (
                          <Button
                            variant="secondary"
                            onPress={openai.verify}
                            isDisabled={openai.pending}
                          >
                            Verify with Google
                          </Button>
                        ) : (
                          <Button
                            variant="secondary"
                            onPress={() => {
                              void openai.prepareReveal();
                            }}
                            isDisabled={!openai.configured || openai.pending}
                          >
                            Reveal
                          </Button>
                        )}
                      </div>
                      <Description>
                        Reveal requires another Google verification with this
                        same admin account. The key clears after 30 seconds,
                        Hide, leaving Credentials, closing settings, sign-out,
                        account change, or hiding this browser tab.
                      </Description>
                    </div>
                  </Card.Content>
                </Card>
              </div>
            </Tabs.Panel>
            <Tabs.Panel id="usage">
              <div className="grid gap-4">
                <div className="grid gap-2">
                  <Typography type="h5" render={(props) => <h3 {...props} />}>
                    OpenAI usage and limits
                  </Typography>
                  <Description>
                    The known-cost stop threshold is not a guaranteed cap.
                    Unknown is not zero.
                  </Description>
                </div>
                {refresh.usage ? (
                  <div className="grid gap-2">
                    <Typography type="h6" render={(props) => <h4 {...props} />}>
                      Current operator limits
                    </Typography>
                    <p>
                      {refresh.usage.default_request_limit} attempts per run · $
                      {refresh.usage.default_known_cost_stop_usd} USD known-cost
                      stop
                    </p>
                    {refresh.usage.daily_cost_stop_invalid === true && (
                      <Alert status="warning">
                        <Alert.Indicator />
                        <Alert.Content>
                          <Alert.Title>
                            Daily cost stop misconfigured
                          </Alert.Title>
                          <Alert.Description>
                            WW_OPENAI_DAILY_COST_STOP_USD is not a USD amount
                            between 0.01 and 1000. New OpenAI extraction is
                            refused until the operator corrects it.
                          </Alert.Description>
                        </Alert.Content>
                      </Alert>
                    )}
                    {typeof refresh.usage.daily_cost_stop_usd === 'string' && (
                      <p>
                        ${refresh.usage.daily_cost_stop_usd} USD rolling 24-hour
                        known-cost stop · ${refresh.usage.rolling_24h_known_usd}{' '}
                        USD known in the last 24 hours (
                        {refresh.usage.rolling_24h_attempts} attempts,{' '}
                        {refresh.usage.rolling_24h_unpriced_attempts} unpriced)
                      </p>
                    )}
                    <Description>
                      Most recent 20 runs; older records and saved checkpoints
                      remain. No automatic resume.
                    </Description>
                    {refresh.usage.runs.length === 0 && (
                      <p>No recorded OpenAI runs.</p>
                    )}
                    <Accordion>
                      {refresh.usage.runs.map((run) => (
                        <Accordion.Item key={run.run_id} id={run.run_id}>
                          <Accordion.Heading>
                            <Accordion.Trigger>
                              <span className="grid gap-1">
                                <span>Run {run.run_id.slice(0, 8)}</span>
                                <span className="break-all">
                                  {run.attempts}/{run.request_limit} attempts ·
                                  ${run.known_usd} USD known
                                </span>
                              </span>
                              <Chip
                                color={
                                  run.total_usd === null ? 'warning' : 'default'
                                }
                                size="sm"
                              >
                                {run.total_usd === null
                                  ? 'Cost incomplete'
                                  : 'Known estimate'}
                              </Chip>
                              <Accordion.Indicator />
                            </Accordion.Trigger>
                          </Accordion.Heading>
                          <Accordion.Panel>
                            <Accordion.Body>
                              <p className="break-all">Run ID: {run.run_id}</p>
                              <p className="break-all">
                                Total USD:{' '}
                                {run.total_usd === null
                                  ? 'unknown'
                                  : `$${run.total_usd}`}
                                ; known-cost stop: ${run.known_cost_stop_usd};
                                unpriced attempts: {run.unpriced_attempts}.
                              </p>
                              <Description>
                                Input: {run.input_tokens ?? 'unknown'} (includes
                                cached read: {run.cached_tokens ?? 'unknown'},
                                cache write:{' '}
                                {run.cache_write_tokens ?? 'unknown'}). Output:{' '}
                                {run.output_tokens ?? 'unknown'} (includes
                                reasoning: {run.reasoning_tokens ?? 'unknown'}).
                              </Description>
                            </Accordion.Body>
                          </Accordion.Panel>
                        </Accordion.Item>
                      ))}
                    </Accordion>
                  </div>
                ) : refresh.usageError ? (
                  <Alert status="warning">
                    <Alert.Indicator />
                    <Alert.Content>
                      <Alert.Title>Usage unavailable</Alert.Title>
                      <Alert.Description>
                        Usage could not be loaded. Unknown is not zero. Check
                        your session or reopen settings.
                      </Alert.Description>
                    </Alert.Content>
                  </Alert>
                ) : (
                  <div
                    className="grid gap-2"
                    role="status"
                    aria-label="Loading usage"
                    aria-busy="true"
                  >
                    {[75, 55, 65].map((width) => (
                      <div
                        key={width}
                        className="skeleton-text-line"
                        style={{ width: `${width}%` }}
                      >
                        <Skeleton>
                          <div className="skeleton-line-space" />
                        </Skeleton>
                      </div>
                    ))}
                  </div>
                )}
                <Accordion>
                  <Accordion.Item id="about-estimates">
                    <Accordion.Heading>
                      <Accordion.Trigger>
                        About these estimates
                        <Accordion.Indicator />
                      </Accordion.Trigger>
                    </Accordion.Heading>
                    <Accordion.Panel>
                      <Accordion.Body>
                        <Description>
                          In-flight calls may add charges after the stop
                          threshold. Partial or failed runs and unknown usage
                          can leave analysis incomplete; usage is not proof of
                          completed analysis. Cached and reasoning token subsets
                          are already included in input/output totals, not extra
                          tokens. Costs are estimates, not invoices.
                        </Description>
                      </Accordion.Body>
                    </Accordion.Panel>
                  </Accordion.Item>
                  <Accordion.Item id="model-pricing">
                    <Accordion.Heading>
                      <Accordion.Trigger>
                        Model and pricing details
                        <Accordion.Indicator />
                      </Accordion.Trigger>
                    </Accordion.Heading>
                    <Accordion.Panel>
                      <Accordion.Body>
                        <p>Model: gpt-5.6-luna · Reasoning: low.</p>
                        <Description>
                          Standard pricing estimate as of 2026-09-09, USD per
                          million tokens: input $0.20; cached read $0.02; cache
                          write $0.25; output $1.20.
                        </Description>
                      </Accordion.Body>
                    </Accordion.Panel>
                  </Accordion.Item>
                </Accordion>
              </div>
            </Tabs.Panel>
            <Tabs.Panel id="refresh">
              <div className="grid gap-4">
                <Card variant="secondary">
                  <Card.Header>
                    <Card.Title>Current Duo approval</Card.Title>
                    <Card.Description>
                      {refresh.challenge?.approval_required
                        ? 'Approve only the refresh you requested, in Duo Mobile. No additional push will be sent.'
                        : 'No current approval code. Start an explicit Refresh from the workspace when ready; status reads never sign in.'}
                    </Card.Description>
                  </Card.Header>
                  <Card.Content>
                    <p>
                      One Refresh collects jobs, then automatically extracts
                      saved details. Duo approval may be required. Limits or
                      unknown usage can leave analysis incomplete.
                    </p>
                    {refresh.challenge?.code && (
                      <TextField isReadOnly value={refresh.challenge.code}>
                        <Label>Current Duo verification code</Label>
                        <Input autoComplete="off" spellCheck={false} />
                      </TextField>
                    )}
                    <Description>
                      Authentication expires within 3 minutes overall. The code
                      clears on expiry, close, sign-out, account change or
                      hiding this browser tab.
                    </Description>
                    <p role="status">{refresh.message}</p>
                  </Card.Content>
                </Card>
                <RefreshPacingCard pacing={refresh.pacing} />
              </div>
            </Tabs.Panel>
          </Tabs>
        </AdminSettingsDialog>
      )}
    </>
  );
}

export function AuthenticatedApp() {
  const { state, signIn, signOut } = useAppAuth();
  const session =
    state.status === 'ready' || state.status === 'refreshing'
      ? state.session
      : null;
  const profile = useAccountProfile(session?.uid ?? null);
  const adminSettings = useRef<AdminSettingsHandle>(null);
  const openAdminSettings = useCallback(
    () => adminSettings.current?.open(),
    [],
  );
  return (
    <AuthGate state={state} signIn={signIn}>
      {session && (
        <App
          key={session.uid}
          onOpenAdminSettings={
            session.role === 'admin' ? openAdminSettings : undefined
          }
          account={
            <AccountMenu
              profile={profile}
              signOut={signOut}
              role={session.role}
              uid={session.uid}
              settingsRef={adminSettings}
            />
          }
        />
      )}
    </AuthGate>
  );
}
