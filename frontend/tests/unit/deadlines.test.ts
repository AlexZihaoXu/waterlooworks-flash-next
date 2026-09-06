import { describe, expect, it } from 'vitest';
import {
  deadlineBadge,
  parseDeadline,
  compareRemaining,
} from '../../src/lib/deadlines';

// Calendar and duration arithmetic only, not fabricated postings or API responses.
describe('deadline handling', () => {
  it('interprets the verified display format in Eastern Time, including DST', () => {
    expect(parseDeadline('Jan 15, 2026 09:00 AM')).toBe(
      Date.UTC(2026, 0, 15, 14),
    );
    expect(parseDeadline('Jul 15, 2026 09:00 AM')).toBe(
      Date.UTC(2026, 6, 15, 13),
    );
  });
  it('distinguishes midnight/noon and accepts valid leap days', () => {
    expect(parseDeadline('Jan 15, 2026 12:00 AM')).toBe(
      Date.UTC(2026, 0, 15, 5),
    );
    expect(parseDeadline('Jan 15, 2026 12:00 PM')).toBe(
      Date.UTC(2026, 0, 15, 17),
    );
    expect(parseDeadline('Feb 29, 2024 09:00 AM')).toBe(
      Date.UTC(2024, 1, 29, 14),
    );
  });
  it('rejects unknown, impossible, nonexistent, and ambiguous local times', () => {
    for (const value of [
      null,
      {},
      '',
      '2026-07-15',
      'Feb 30, 2026 09:00 AM',
      'Jan 15, 2026 00:00 AM',
      'Jan 15, 2026 13:00 PM',
      'Jan 15, 2026 09:60 AM',
      'Mar 8, 2026 02:30 AM',
      'Nov 1, 2026 01:30 AM',
    ]) {
      expect(parseDeadline(value)).toBeNull();
    }
  });
  it('uses conservative whole days/hours and honest unknown/passed labels', () => {
    expect(deadlineBadge(49 * 3600000, 0).label).toBe('2 days left');
    expect(deadlineBadge(25 * 3600000, 0).label).toBe('1 day left');
    expect(deadlineBadge(2 * 3600000, 0).label).toBe('2 hours left');
    expect(deadlineBadge(3600000, 0).label).toBe('1 hour left');
    expect(deadlineBadge(1, 0).label).toBe('Under 1 hour left');
    expect(deadlineBadge(0, 0).label).toBe('Deadline passed');
    expect(deadlineBadge(null, 0).label).toBe('Deadline unknown');
  });
  it('reserves emphasis for approaching deadlines', () => {
    const hour = 3600000;
    expect(deadlineBadge(73 * hour, 0)).toMatchObject({ quiet: true });
    expect(deadlineBadge(72 * hour, 0)).toMatchObject({
      quiet: false,
      color: 'warning',
      variant: 'soft',
    });
    expect(deadlineBadge(24 * hour, 0)).toMatchObject({ color: 'warning' });
    expect(deadlineBadge(24 * hour - 1, 0)).toMatchObject({
      color: 'danger',
      variant: 'soft',
    });
    expect(deadlineBadge(hour - 1, 0)).toMatchObject({
      color: 'danger',
      variant: 'primary',
    });
    expect(deadlineBadge(null, 0)).toMatchObject({ quiet: true });
    expect(deadlineBadge(0, 0)).toMatchObject({ quiet: true });
  });
  it('keeps exactly-now with passed deadlines, before unknowns', () => {
    const now = Date.UTC(2026, 0, 1);
    const hour = 3600000;
    const values = [null, now, now - hour, now + 48 * hour, now + hour];
    expect(values.sort((a, b) => compareRemaining(a, b, now))).toEqual([
      now + hour,
      now + 48 * hour,
      now,
      now - hour,
      null,
    ]);
  });
  it('puts upcoming deadlines first, then passed, then unknown', () => {
    expect(compareRemaining(20, 30, 10)).toBeLessThan(0);
    expect(compareRemaining(20, 0, 10)).toBeLessThan(0);
    expect(compareRemaining(0, null, 10)).toBeLessThan(0);
    expect(compareRemaining(null, null, 10)).toBe(0);
  });
});
