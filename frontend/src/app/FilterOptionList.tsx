import {
  memo,
  useEffect,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react';
import { Checkbox, ScrollShadow, Typography } from '@heroui/react';
import {
  OPTION_ROW_HEIGHT,
  visibleRows,
  type OptionRow,
} from '../lib/optionGroups';

// Extracted existing HeroUI composition: scroll state stays out of App.
// Memoized rows retain the same checkbox DOM while the surrounding window moves.
const Option = memo(function Option({
  row,
  selected,
  width,
  setDraft,
}: {
  row: OptionRow;
  selected: boolean;
  width: number;
  setDraft: Dispatch<SetStateAction<string[]>>;
}) {
  if (row.kind === 'heading')
    return (
      <div className="picker-letter">
        <Typography type="h6" role="heading" aria-level={3}>
          {row.label} ({row.count})
        </Typography>
      </div>
    );
  return (
    <div className="picker-option" title={row.value}>
      <Checkbox
        variant="secondary"
        isSelected={selected}
        aria-label={row.value}
        aria-description={`Group ${row.group}`}
        onChange={(checked) =>
          setDraft((previous) =>
            checked
              ? [...new Set([...previous, row.value])]
              : previous.filter((value) => value !== row.value),
          )
        }
      >
        <Checkbox.Content>
          <Checkbox.Control>
            <Checkbox.Indicator />
          </Checkbox.Control>
          <span
            className="picker-option-label"
            style={{ maxWidth: width ? Math.max(0, width - 64) : undefined }}
          >
            {row.value}
          </span>
        </Checkbox.Content>
      </Checkbox>
    </div>
  );
});

export const FilterOptionList = memo(function FilterOptionList({
  rows,
  label,
  draft,
  setDraft,
}: {
  rows: readonly OptionRow[];
  label: string;
  draft: readonly string[];
  setDraft: Dispatch<SetStateAction<string[]>>;
}) {
  const [viewport, setViewport] = useState<HTMLDivElement | null>(null);
  const [window, setWindow] = useState(() => ({
    width: 0,
    range: visibleRows(rows.length, 0, 0),
  }));
  useEffect(() => {
    if (!viewport) return;
    let frame: number | null = null;
    const measure = () => {
      frame = null;
      const width = viewport.clientWidth;
      const range = visibleRows(
        rows.length,
        viewport.clientHeight,
        viewport.scrollTop,
      );
      setWindow((previous) =>
        previous.width === width &&
        previous.range.start === range.start &&
        previous.range.end === range.end &&
        previous.range.bottom === range.bottom
          ? previous
          : { width, range },
      );
    };
    const schedule = () => {
      if (frame === null) frame = requestAnimationFrame(measure);
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(viewport);
    viewport.addEventListener('scroll', schedule, { passive: true });
    schedule();
    return () => {
      observer.disconnect();
      viewport.removeEventListener('scroll', schedule);
      if (frame !== null) cancelAnimationFrame(frame);
    };
  }, [viewport, rows.length]);
  const { range, width } = window;
  return (
    <div className="picker-options">
      <ScrollShadow
        size={24}
        ref={setViewport}
        role="group"
        aria-label={label}
        tabIndex={0}
      >
        <div aria-hidden="true" style={{ height: range.top }} />
        <div
          className="picker-list"
          style={{ gridAutoRows: OPTION_ROW_HEIGHT }}
        >
          {rows.slice(range.start, range.end).map((row) => (
            <Option
              key={row.key}
              row={row}
              selected={row.kind === 'option' && draft.includes(row.value)}
              width={width}
              setDraft={setDraft}
            />
          ))}
        </div>
        <div aria-hidden="true" style={{ height: range.bottom }} />
        {rows.length === 0 && (
          <Typography type="body-sm">No matching options</Typography>
        )}
      </ScrollShadow>
    </div>
  );
});
