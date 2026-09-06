import { memo } from 'react';
import { Chip, Typography } from '@heroui/react';
import { logPresentation } from '../lib/logPresentation';

// Shared HeroUI composition for the compact preview and full console.
export const RefreshLogEntry = memo(function RefreshLogEntry({
  time,
  level,
  message,
}: {
  time: string;
  level: keyof typeof logPresentation;
  message: string;
}) {
  const style = logPresentation[level];
  return (
    <span className="console-entry">
      <Typography type="code" color="muted">
        [{time}]
      </Typography>
      {style.muted ? (
        <Typography type="code" color="muted">
          {level.toUpperCase()}
        </Typography>
      ) : (
        <Chip size="sm" variant={style.variant} color={style.color}>
          {level.toUpperCase()}
        </Chip>
      )}
      <Typography type="code" color={style.muted ? 'muted' : 'default'}>
        {message}
      </Typography>
    </span>
  );
});
