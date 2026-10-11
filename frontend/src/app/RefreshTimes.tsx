import { Tooltip, Typography } from '@heroui/react';
import { refreshTimesText, torontoDateTime } from '../lib/refreshSchedule';
import { useRelativeNow } from '../lib/useRelativeNow';

/**
 * "Refreshed 3 hours ago · Next in 5 hours" with the exact Toronto times in
 * the documented focusable HeroUI Tooltip (the deadline pattern). Not a live
 * region: the label ticks without announcements.
 */
export function RefreshTimes({
  lastRefreshed,
  nextScheduled,
  narrow = false,
}: {
  lastRefreshed: string | null | undefined;
  nextScheduled: string | null | undefined;
  narrow?: boolean;
}) {
  const now = useRelativeNow([lastRefreshed, nextScheduled]);
  const text = refreshTimesText(lastRefreshed, nextScheduled, now, narrow);
  const next = nextScheduled ? Date.parse(nextScheduled) : Number.NaN;
  const exact = [
    lastRefreshed
      ? `Last refreshed ${torontoDateTime(lastRefreshed)}`
      : 'Never refreshed',
    !Number.isNaN(next) && next >= now && nextScheduled
      ? `Next scheduled refresh ${torontoDateTime(nextScheduled)}`
      : null,
  ]
    .filter(Boolean)
    .join('. ');
  return (
    <Tooltip delay={300}>
      <Tooltip.Trigger aria-label={`${text}. ${exact}.`}>
        <Typography type="body-xs" color="muted" weight="normal">
          {text}
        </Typography>
      </Tooltip.Trigger>
      <Tooltip.Content showArrow>
        <Tooltip.Arrow />
        {exact}
      </Tooltip.Content>
    </Tooltip>
  );
}
