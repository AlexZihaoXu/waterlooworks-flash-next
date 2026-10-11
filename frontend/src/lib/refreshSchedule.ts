import type { components } from './api.generated';

export type RefreshScheduleView = components['schemas']['RefreshScheduleView'];
export type ScheduleSlot = components['schemas']['ScheduleSlotView'];

export const TORONTO = 'America/Toronto';
const HHMM = /^([01][0-9]|2[0-3]):([0-5][0-9])$/;
const OUTCOMES = [
  'claimed',
  'started',
  'skipped_running',
  'skipped_blocked',
  'skipped_disabled',
  'failed_to_start',
];
const FAILURES = [
  'sign_in',
  'authentication',
  'rate_limit',
  'redirect',
  'ban_risk',
];

/** "07:30" -> "7:30 AM" (12-hour display of a Toronto wall-clock time). */
export function scheduleTimeLabel(value: string): string {
  const match = HHMM.exec(value);
  if (!match) return value;
  const hour = Number(match[1]);
  return `${hour % 12 || 12}:${match[2]} ${hour < 12 ? 'AM' : 'PM'}`;
}

const torontoFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: TORONTO,
  month: 'short',
  day: 'numeric',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  timeZoneName: 'short',
});

/** Exact instant in Toronto time, e.g. "Oct 10, 2026, 7:30 AM EDT". */
export function torontoDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? 'unknown time'
    : torontoFormat.format(date);
}

const isTime = (value: unknown) =>
  typeof value === 'string' &&
  value.length <= 64 &&
  !Number.isNaN(Date.parse(value));
const isRunId = (value: unknown) =>
  typeof value === 'string' && /^[a-zA-Z0-9-]{1,64}$/.test(value);
const isSlot = (value: unknown) =>
  typeof value === 'string' && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(value);

/** Strict admin schedule view: fixed enums, sorted HH:MM times, ISO instants. */
export function parseScheduleView(value: unknown): RefreshScheduleView {
  if (!value || typeof value !== 'object')
    throw new Error('Schedule unavailable.');
  const data = value as Record<string, unknown>;
  const times = data.times;
  const failure = data.failure as Record<string, unknown> | null;
  if (
    data.timezone !== TORONTO ||
    !Array.isArray(times) ||
    times.length > 24 ||
    !times.every((time) => typeof time === 'string' && HHMM.test(time)) ||
    times.some((time, index) => index > 0 && times[index - 1] >= time) ||
    typeof data.enabled !== 'boolean' ||
    typeof data.server_enabled !== 'boolean' ||
    !(data.next_run_at === null || isTime(data.next_run_at)) ||
    !Number.isSafeInteger(data.min_interval_minutes) ||
    !Number.isSafeInteger(data.max_times) ||
    !Array.isArray(data.last) ||
    data.last.length > 20 ||
    !data.last.every(
      (slot: Record<string, unknown>) =>
        slot &&
        isSlot(slot.slot) &&
        isTime(slot.due_at) &&
        OUTCOMES.includes(String(slot.outcome)) &&
        (slot.run_id === null || isRunId(slot.run_id)) &&
        (slot.failure_category === null ||
          FAILURES.includes(String(slot.failure_category))),
    ) ||
    !(
      failure === null ||
      (typeof failure === 'object' &&
        isRunId(failure.run_id) &&
        isSlot(failure.slot) &&
        isTime(failure.failed_at) &&
        FAILURES.includes(String(failure.category)))
    )
  )
    throw new Error('Schedule unavailable.');
  return data as RefreshScheduleView;
}

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['day', 86_400_000],
  ['hour', 3_600_000],
  ['minute', 60_000],
];
const longFormat = new Intl.RelativeTimeFormat('en', { numeric: 'always' });
const narrowFormat = new Intl.RelativeTimeFormat('en', {
  numeric: 'always',
  style: 'narrow',
});

/** Largest whole unit: seconds under a minute, minutes under an hour, etc. */
export function relativeTime(
  target: number,
  now: number,
  narrow = false,
): string {
  const offset = target - now;
  const format = narrow ? narrowFormat : longFormat;
  for (const [unit, size] of UNITS)
    if (Math.abs(offset) >= size)
      return format.format(Math.trunc(offset / size), unit);
  // Avoid "-0 seconds"; a zero offset reads as upcoming.
  return format.format(Math.trunc(offset / 1000) || 0, 'second');
}

/** "Refreshed 3 hours ago · Next in 5 hours"; next only while in the future. */
export function refreshTimesText(
  lastRefreshed: string | null | undefined,
  nextScheduled: string | null | undefined,
  now: number,
  narrow = false,
): string {
  const last = lastRefreshed ? Date.parse(lastRefreshed) : Number.NaN;
  const next = nextScheduled ? Date.parse(nextScheduled) : Number.NaN;
  const parts = [
    Number.isNaN(last)
      ? 'Never refreshed'
      : `Refreshed ${relativeTime(last, now, narrow)}`,
  ];
  if (!Number.isNaN(next) && next >= now)
    parts.push(
      `${narrow ? 'next' : 'Next'} ${relativeTime(next, now, narrow)}`,
    );
  return parts.join(' · ');
}

/** Tick every second while any value is under a minute away, else 30 s. */
export function tickDelay(
  values: readonly (string | null | undefined)[],
  now: number,
): number {
  return values.some((value) => {
    const time = value ? Date.parse(value) : Number.NaN;
    return !Number.isNaN(time) && Math.abs(time - now) < 60_000;
  })
    ? 1_000
    : 30_000;
}
