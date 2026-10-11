import { use, useMemo, useState } from 'react';
import { LuX } from 'react-icons/lu';
import {
  Alert,
  Button,
  Description,
  FieldError,
  Form,
  Label,
  ScrollShadow,
  Switch,
  TimeField,
  Typography,
  type TimeValue,
} from '@heroui/react';
import { RefreshContext } from '../lib/refreshContext';
import {
  scheduleTimeLabel,
  torontoDateTime,
  type RefreshScheduleView,
  type ScheduleSlot,
} from '../lib/refreshSchedule';
import type { useRefreshSchedule } from '../lib/useRefreshSchedule';
import { RefreshConsole } from './RefreshActivity';
import { RefreshLogEntry } from './RefreshLogEntry';

type Failure = NonNullable<RefreshScheduleView['failure']>;

const FAILURE_REASONS: Record<Failure['category'], string> = {
  sign_in: 'WaterlooWorks sign-in or Duo approval did not complete.',
  authentication:
    'WaterlooWorks rejected the session mid-run (401/403 or a sign-in page).',
  rate_limit:
    'WaterlooWorks rate-limited the refresh (429 or a challenge page).',
  redirect: 'WaterlooWorks redirected to a login page mid-run.',
  ban_risk: 'WaterlooWorks returned a ban-risk warning; refreshes are blocked.',
};

const OUTCOMES: Record<ScheduleSlot['outcome'], string> = {
  claimed: 'deciding',
  started: 'started',
  skipped_running: 'skipped: a refresh was already running',
  skipped_blocked: 'skipped: cooldown, budget or block active',
  skipped_disabled: 'skipped: refresh disabled on this server',
  failed_to_start: 'could not start (credentials or AI settings)',
};

function hhmm(value: TimeValue): string {
  return `${String(value.hour).padStart(2, '0')}:${String(value.minute).padStart(2, '0')}`;
}

/** Latest in-memory refresh log, shown only when it belongs to the failed run. */
function FailedRunLog({ runId }: { runId: string }) {
  const refresh = use(RefreshContext);
  const sameRun = refresh?.run_id === runId;
  const logs = refresh?.logs;
  const entries = useMemo(
    () =>
      sameRun && logs
        ? logs.map((log) => ({
            key: `${runId}-${log.sequence}`,
            content: (
              <RefreshLogEntry
                time={new Date(log.at).toLocaleTimeString('en-GB')}
                level={log.level}
                message={log.message}
              />
            ),
          }))
        : null,
    [sameRun, logs, runId],
  );
  if (!refresh || entries === null)
    return (
      <Description>
        That run&apos;s log is no longer available: the server keeps only the
        latest run&apos;s log in memory, and it restarted or a newer run started
        since.
      </Description>
    );
  return (
    <RefreshConsole
      entries={entries}
      status={refresh.status}
      revision={`${runId}:${refresh.logs.at(-1)?.sequence ?? 0}`}
    />
  );
}

// Documented HeroUI Switch/Alert/ScrollShadow/TimeField/Button composition.
export function RefreshScheduleTab({
  schedule,
  compact,
}: {
  schedule: ReturnType<typeof useRefreshSchedule>;
  compact: boolean;
}) {
  const { view, pending, error } = schedule;
  const [draft, setDraft] = useState<TimeValue | null>(null);
  const [errorSource, setErrorSource] = useState<'add' | 'other'>('other');
  const [showLog, setShowLog] = useState(false);
  if (!view)
    return <p role="status">{error || 'Loading the refresh schedule…'}</p>;
  const failure = view.failure;
  const add = async () => {
    if (!draft || pending) return;
    setErrorSource('add');
    if (await schedule.add(hhmm(draft))) setDraft(null);
  };
  return (
    <div className="grid gap-4">
      <div className="grid gap-2">
        <Typography type="h5" render={(props) => <h3 {...props} />}>
          Scheduled refresh
        </Typography>
        <Description>Times are in Toronto time (ET).</Description>
      </div>
      <Switch
        isSelected={view.enabled}
        isDisabled={pending}
        onChange={(enabled) => {
          setErrorSource('other');
          void schedule.setEnabled(enabled);
        }}
      >
        <Switch.Content>
          <Switch.Control>
            <Switch.Thumb />
          </Switch.Control>
          <Label>Run refreshes automatically at these times</Label>
        </Switch.Content>
        <Description>
          Each run uses the same guarded path as Refresh: pacing, caps, the{' '}
          {view.min_interval_minutes}-minute minimum interval and blocks still
          apply. A run may send a Duo push if WaterlooWorks needs a new sign-in.
        </Description>
      </Switch>
      {!view.server_enabled && (
        <Alert status="warning">
          <Alert.Indicator />
          <Alert.Content>
            <Alert.Title>Scheduling is turned off on this server</Alert.Title>
            <Alert.Description>
              {view.enabled
                ? 'The switch is on, but nothing runs until the operator sets WW_ENABLE_REFRESH=1 and WW_ENABLE_SCHEDULE=1.'
                : 'Even when switched on, nothing runs until the operator sets WW_ENABLE_REFRESH=1 and WW_ENABLE_SCHEDULE=1.'}
            </Alert.Description>
          </Alert.Content>
        </Alert>
      )}
      {failure && (
        <Alert status="danger">
          <Alert.Indicator />
          <Alert.Content>
            <Alert.Title>
              Scheduled refresh at {torontoDateTime(failure.failed_at)} failed.
              Scheduling was turned off.
            </Alert.Title>
            <Alert.Description>
              {FAILURE_REASONS[failure.category]} Check the log, then switch
              scheduling back on.
            </Alert.Description>
            <div>
              <Button
                size={compact ? 'lg' : 'sm'}
                variant="danger-soft"
                onPress={() => setShowLog((shown) => !shown)}
              >
                {showLog ? 'Hide log' : 'View log'}
              </Button>
            </div>
          </Alert.Content>
        </Alert>
      )}
      {failure && showLog && <FailedRunLog runId={failure.run_id} />}
      <p role="status">
        {view.next_run_at
          ? `Next run: ${torontoDateTime(view.next_run_at)}.`
          : view.enabled && view.server_enabled
            ? 'No upcoming run: add a time.'
            : 'No upcoming run while scheduling is off.'}
      </p>
      {view.times.length === 0 ? (
        <p>No scheduled refreshes.</p>
      ) : (
        <div className="schedule-times">
          <ScrollShadow size={24} tabIndex={0} aria-label="Scheduled times">
            <ul className="schedule-time-list">
              {view.times.map((time) => {
                const label = scheduleTimeLabel(time);
                return (
                  <li key={time} className="schedule-time-row">
                    <Typography type="body-sm">{label}</Typography>
                    <Button
                      isIconOnly
                      variant="ghost"
                      size={compact ? 'lg' : 'sm'}
                      aria-label={`Remove ${label}`}
                      isDisabled={pending}
                      onPress={() => {
                        setErrorSource('other');
                        void schedule.remove(time);
                      }}
                    >
                      <LuX aria-hidden="true" />
                    </Button>
                  </li>
                );
              })}
            </ul>
          </ScrollShadow>
        </div>
      )}
      <Form
        aria-label="Add a scheduled time"
        onSubmit={(event) => {
          event.preventDefault();
          void add();
        }}
      >
        <div className="schedule-add-row">
          <TimeField
            value={draft}
            onChange={setDraft}
            hourCycle={12}
            granularity="minute"
            isInvalid={errorSource === 'add' && error !== ''}
            isDisabled={pending}
            validationBehavior="aria"
          >
            <Label>Time to add (Toronto)</Label>
            <TimeField.Group>
              <TimeField.Input>
                {(segment) => <TimeField.Segment segment={segment} />}
              </TimeField.Input>
            </TimeField.Group>
            <FieldError>{errorSource === 'add' ? error : ''}</FieldError>
          </TimeField>
          <Button
            type="submit"
            size={compact ? 'lg' : 'md'}
            isDisabled={!draft || pending}
            isPending={pending && errorSource === 'add'}
          >
            Add
          </Button>
        </div>
      </Form>
      {errorSource === 'other' && error && <p role="alert">{error}</p>}
      {view.last.length > 0 && (
        <div className="grid gap-1">
          <Typography type="h6" render={(props) => <h4 {...props} />}>
            Recent scheduled runs
          </Typography>
          {view.last.map((slot) => (
            <Description key={slot.slot}>
              {torontoDateTime(slot.due_at)}: {OUTCOMES[slot.outcome]}
              {slot.failure_category ? ' · turned scheduling off' : ''}
            </Description>
          ))}
        </div>
      )}
    </div>
  );
}
