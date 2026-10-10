import { parseDateTime, toZoned } from '@internationalized/date';

// Verified listing display format; Waterloo's FCS schedule specifies Eastern Time.
// Keep source text unchanged. Never use the viewer's timezone or a fixed EST offset.
const MONTHS = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];
export const DEADLINE_ZONE = 'America/Toronto';

export function parseDeadline(value: unknown): number | null {
  if (typeof value !== 'string') return null;
  const match =
    /^([A-Z][a-z]{2}) (\d{1,2}), (\d{4}) (\d{1,2}):(\d{2}) (AM|PM)$/.exec(
      value.trim(),
    );
  if (!match) return null;
  const month = MONTHS.indexOf(match[1] ?? '') + 1;
  const hour = Number(match[4]);
  if (!month || hour < 1 || hour > 12) return null;
  const hours = (hour % 12) + (match[6] === 'PM' ? 12 : 0);
  const iso = `${match[3]}-${String(month).padStart(2, '0')}-${String(match[2]).padStart(2, '0')}T${String(hours).padStart(2, '0')}:${match[5]}`;
  try {
    return toZoned(parseDateTime(iso), DEADLINE_ZONE, 'reject')
      .toDate()
      .getTime();
  } catch {
    // Invalid calendars, DST gaps/repeated times, and unknown formats stay unknown.
    return null;
  }
}

export function deadlineBadge(
  deadline: number | null,
  now: number,
): {
  label: string;
  color: 'default' | 'warning' | 'danger';
  variant: 'soft' | 'primary';
  quiet: boolean;
} {
  const quiet = { color: 'default', variant: 'soft', quiet: true } as const;
  if (deadline === null || !Number.isFinite(deadline))
    return { label: 'Deadline unknown', ...quiet };
  const remaining = deadline - now;
  if (remaining <= 0) return { label: 'Deadline passed', ...quiet };
  const days = Math.floor(remaining / 86400000);
  const hours = Math.floor(remaining / 3600000);
  const label =
    days >= 1
      ? `${days} day${days === 1 ? '' : 's'} left`
      : hours >= 1
        ? `${hours} hour${hours === 1 ? '' : 's'} left`
        : 'Under 1 hour left';
  if (remaining > 3 * 86400000) return { label, ...quiet };
  return {
    label,
    quiet: false,
    color: remaining >= 86400000 ? 'warning' : 'danger',
    variant: remaining < 3600000 ? 'primary' : 'soft',
  };
}

/** Exact source deadline for keyboard/hover disclosure; source text unchanged. */
export function exactDeadline(deadline: number | null, raw: unknown): string {
  return deadline === null || typeof raw !== 'string' || !raw.trim()
    ? 'Deadline could not be verified'
    : `${raw.trim()} Eastern Time`;
}

export function compareRemaining(
  a: number | null,
  b: number | null,
  now: number,
): number {
  const rank = (value: number | null) =>
    value === null ? 2 : value > now ? 0 : 1;
  const difference = rank(a) - rank(b);
  if (difference) return difference;
  if (a === null || b === null) return 0;
  return a > now ? a - b : b - a;
}
