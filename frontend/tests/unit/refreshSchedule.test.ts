import { describe, expect, it } from 'vitest';
import {
  parseScheduleView,
  refreshTimesText,
  relativeTime,
  scheduleTimeLabel,
  tickDelay,
  torontoDateTime,
} from '../../src/lib/refreshSchedule';

// Schedule metadata only: times, booleans and timestamps. No posting data.
const view = {
  timezone: 'America/Toronto',
  times: ['07:30', '19:00'],
  enabled: true,
  server_enabled: true,
  next_run_at: '2026-10-10T23:00:00+00:00',
  min_interval_minutes: 30,
  max_times: 24,
  last: [
    {
      slot: '2026-10-10 07:30',
      due_at: '2026-10-10T11:30:00+00:00',
      outcome: 'started',
      run_id: 'run-1',
      failure_category: null,
    },
  ],
  failure: null,
};

describe('schedule presentation', () => {
  it.each([
    ['00:00', '12:00 AM'],
    ['07:30', '7:30 AM'],
    ['12:05', '12:05 PM'],
    ['23:59', '11:59 PM'],
  ])('labels %s as %s', (value, label) => {
    expect(scheduleTimeLabel(value)).toBe(label);
  });

  it('shows exact instants in Toronto time with the zone abbreviation', () => {
    expect(torontoDateTime('2026-10-10T11:30:00+00:00')).toBe(
      'Oct 10, 2026, 7:30 AM EDT',
    );
    expect(torontoDateTime('2026-01-15T12:30:00Z')).toBe(
      'Jan 15, 2026, 7:30 AM EST',
    );
  });

  it('accepts a valid view and rejects malformed ones', () => {
    expect(parseScheduleView(view)).toEqual(view);
    for (const broken of [
      null,
      { ...view, times: ['7:30'] },
      { ...view, times: ['19:00', '07:30'] },
      { ...view, enabled: 'yes' },
      { ...view, next_run_at: 'soon' },
      { ...view, last: [{ ...view.last[0], outcome: 'invented' }] },
      {
        ...view,
        failure: {
          run_id: 'r',
          slot: 's',
          failed_at: 'x',
          category: 'ban_risk',
        },
      },
    ])
      expect(() => parseScheduleView(broken)).toThrow();
  });
});

describe('relative refresh times', () => {
  const now = Date.parse('2026-10-10T12:00:00Z');
  it.each([
    [0, 'in 0 seconds'],
    [3_000, 'in 3 seconds'],
    [59_999, 'in 59 seconds'],
    [60_000, 'in 1 minute'],
    [-180_000, '3 minutes ago'],
    [3_599_000, 'in 59 minutes'],
    [3_600_000, 'in 1 hour'],
    [-3 * 3_600_000, '3 hours ago'],
    [5 * 3_600_000 + 59 * 60_000, 'in 5 hours'],
    [86_399_000, 'in 23 hours'],
    [86_400_000, 'in 1 day'],
    [-3 * 86_400_000, '3 days ago'],
  ])('formats an offset of %i ms as %s', (offset, text) => {
    expect(relativeTime(now + offset, now)).toBe(text);
  });

  it('formats the narrow form for phones', () => {
    expect(relativeTime(now - 3 * 3_600_000, now, true)).toBe('3h ago');
    expect(relativeTime(now + 5 * 3_600_000, now, true)).toBe('in 5h');
  });

  it('joins last refresh and next run, omitting a missing or past next run', () => {
    const last = '2026-10-10T09:00:00Z';
    const next = '2026-10-10T17:00:00Z';
    expect(refreshTimesText(last, next, now)).toBe(
      'Refreshed 3 hours ago · Next in 5 hours',
    );
    expect(refreshTimesText(last, null, now)).toBe('Refreshed 3 hours ago');
    expect(refreshTimesText(last, '2026-10-10T11:59:00Z', now)).toBe(
      'Refreshed 3 hours ago',
    );
    expect(refreshTimesText(undefined, next, now)).toBe(
      'Never refreshed · Next in 5 hours',
    );
    expect(refreshTimesText(last, next, now, true)).toBe(
      'Refreshed 3h ago · next in 5h',
    );
  });

  it('ticks every second near a value and every 30 seconds otherwise', () => {
    expect(tickDelay(['2026-10-10T09:00:00Z'], now)).toBe(30_000);
    expect(tickDelay(['2026-10-10T12:00:30Z', null], now)).toBe(1_000);
    expect(tickDelay(['2026-10-10T11:59:30Z'], now)).toBe(1_000);
    expect(tickDelay([undefined, null], now)).toBe(30_000);
  });
});
