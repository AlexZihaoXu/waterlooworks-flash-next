import { useEffect, useId, useRef, useState } from 'react';
import { Card, Separator, Typography, Tooltip } from '@heroui/react';
import type { components } from '../lib/api.generated';
import {
  trendPoints,
  trendWindow,
  trendFrame,
  nearestTrendPoint,
} from '../lib/applicationTrend';
import { displayNumber, displayTime } from '../lib/workspace';

type Observation = components['schemas']['PostingObservation'];

// User-approved custom SVG: HeroUI v3 has no time-series chart component.
// The surrounding card and text use unmodified HeroUI components.
export function ApplicationTrend({
  observations,
}: {
  observations: readonly Observation[];
}) {
  const id = useId();
  const [active, setActive] = useState<number | null>(null);
  const [open, setOpen] = useState(false);
  const chartRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (
        event.target instanceof Node &&
        !chartRef.current?.contains(event.target)
      )
        setOpen(false);
    };
    document.addEventListener('pointerdown', closeOutside);
    return () => document.removeEventListener('pointerdown', closeOutside);
  }, [open]);
  const points = trendPoints(
    observations.map((p) => [Date.parse(p.observed_at), p.application_count]),
  );
  const known = points.filter((p) => p.count !== null);
  const first = points.at(0);
  const latest = points.at(-1);
  const window = trendWindow(points);
  const max = known.reduce((value, p) => Math.max(value, p.count ?? 0), 1);
  const { left, right, width } = trendFrame(displayNumber(max).length);
  const x = (time: number) =>
    first && latest && latest.time > first.time
      ? left +
        ((right - left) * (time - first.time)) / (latest.time - first.time)
      : width / 2;
  const y = (count: number) => 130 - (112 * count) / max;
  const ticks = [...new Set([0, Math.ceil(max / 2), max])];
  const selected = known[active ?? known.length - 1];
  const pick = (element: SVGSVGElement, clientX: number, clientY: number) => {
    const matrix = element.getScreenCTM();
    if (!matrix) return;
    const position = new DOMPoint(clientX, clientY).matrixTransform(
      matrix.inverse(),
    );
    setActive(
      nearestTrendPoint(
        known.map((p) => [x(p.time), y(p.count!)]),
        position.x,
        position.y,
      ),
    );
  };
  const stamp = (time: number) => displayTime(new Date(time).toISOString());
  const shortDate = (time: number) =>
    new Date(time).toLocaleDateString('en-CA', {
      month: 'short',
      day: 'numeric',
      timeZone: 'UTC',
    });
  return (
    <Card>
      <Card.Header>
        <Card.Title>Application trend</Card.Title>
      </Card.Header>
      <Separator />
      <Card.Content>
        <div className="stack-small" ref={chartRef}>
          {known.length && first && latest ? (
            <>
              <Tooltip delay={0} isOpen={open} onOpenChange={setOpen}>
                <Tooltip.Trigger
                  aria-label="Explore saved application counts"
                  role="group"
                  onKeyDown={(event) => {
                    if (event.key === 'Escape') {
                      setOpen(false);
                      event.stopPropagation();
                    }
                  }}
                >
                  <svg
                    onPointerMove={(event) => {
                      if (event.pointerType !== 'touch')
                        pick(event.currentTarget, event.clientX, event.clientY);
                    }}
                    onClick={(event) => {
                      pick(event.currentTarget, event.clientX, event.clientY);
                      setOpen(true);
                    }}
                    className="application-trend"
                    viewBox={`0 0 ${width} 164`}
                    role="group"
                    aria-labelledby={`${id}-title ${id}-description`}
                  >
                    <title id={`${id}-title`}>Saved application counts</title>
                    <desc id={`${id}-description`}>
                      Time increases left to right. Counts start at zero. Dashed
                      lines connect saved observations only; unknown counts
                      break the line. Hover, tap, or focus a point for its count
                      and saved time.
                    </desc>
                    {ticks.map((tick) => (
                      <g key={tick}>
                        <line
                          x1={left}
                          x2={right}
                          y1={y(tick)}
                          y2={y(tick)}
                          stroke="var(--separator)"
                        />
                        <text
                          x={left - 6}
                          y={y(tick) + 4}
                          textAnchor="end"
                          fill="var(--muted)"
                        >
                          {displayNumber(tick)}
                        </text>
                      </g>
                    ))}
                    {points.map((point, index) => {
                      const previous = points[index - 1];
                      return point.count !== null && previous?.count != null ? (
                        <line
                          key={index}
                          x1={x(previous.time)}
                          y1={y(previous.count)}
                          x2={x(point.time)}
                          y2={y(point.count)}
                          stroke="var(--accent)"
                          strokeWidth="1.5"
                          strokeDasharray="3 3"
                        />
                      ) : null;
                    })}
                    {open && selected && (
                      <line
                        x1={x(selected.time)}
                        x2={x(selected.time)}
                        y1="18"
                        y2="130"
                        stroke="var(--muted)"
                        strokeDasharray="2 3"
                      />
                    )}
                    {known.map((point, index) => (
                      <circle
                        key={index}
                        cx={x(point.time)}
                        cy={y(point.count!)}
                        r={open && selected === point ? 5 : 3}
                        onFocus={() => {
                          setActive(index);
                          setOpen(true);
                        }}
                        onBlur={() => setOpen(false)}
                        fill="var(--accent)"
                        tabIndex={0}
                        role="img"
                        aria-label={`${displayNumber(point.count)} applications · ${stamp(point.time)}`}
                      />
                    ))}
                    <text x={left} y="154" fill="var(--muted)">
                      {shortDate(first.time)}
                    </text>
                    {latest.time !== first.time && (
                      <text
                        x={right}
                        y="154"
                        textAnchor="end"
                        fill="var(--muted)"
                      >
                        {shortDate(latest.time)}
                      </text>
                    )}
                  </svg>
                </Tooltip.Trigger>
                <Tooltip.Content placement="top" showArrow>
                  <Tooltip.Arrow />
                  {selected && (
                    <div>
                      <Typography type="body-sm" weight="semibold">
                        {displayNumber(selected.count)} applications
                      </Typography>
                      <Typography type="body-xs">
                        {new Date(selected.time).toLocaleString('en-CA', {
                          year: 'numeric',
                          month: 'short',
                          day: 'numeric',
                          hour: 'numeric',
                          minute: '2-digit',
                          second: '2-digit',
                          timeZone: 'UTC',
                        })}{' '}
                        UTC
                      </Typography>
                    </div>
                  )}
                </Tooltip.Content>
              </Tooltip>
              <Typography type="body-xs" color="muted">
                Saved observations · UTC
              </Typography>
              <Typography type="body-sm">
                {window
                  ? `${window.delta > 0 ? '+' : ''}${displayNumber(window.delta)} net over ${window.hours === 24 ? '24h' : `${Number(window.hours.toFixed(2)) || '<0.01'}h observed`}`
                  : '24h change unavailable'}
              </Typography>
              <Typography type="body-xs" color="muted">
                24h window ending {stamp(latest.time)}
                {window?.hours !== 24 ? ' · incomplete coverage' : ''}
              </Typography>
              {observations.some(
                (p) => p.timestamp_provenance !== 'response_received',
              ) && (
                <Typography type="body-xs" color="muted">
                  Includes saved/legacy timestamps.
                </Typography>
              )}
            </>
          ) : (
            <Typography type="body-sm" color="muted">
              No saved numeric application counts yet.
            </Typography>
          )}
        </div>
      </Card.Content>
    </Card>
  );
}
