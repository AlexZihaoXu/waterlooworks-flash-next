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
  categorySelection,
  toggleCategory,
  visibleRows,
  type OptionRow,
} from '../lib/optionGroups';

// Extracted existing HeroUI composition: scroll state stays out of App.
// Memoized rows retain the same checkbox DOM while the surrounding window moves.
const Option = memo(function Option({
  row,
  selected,
  indeterminate,
  width,
  flag,
  setDraft,
}: {
  row: OptionRow;
  selected: boolean;
  indeterminate: boolean;
  width: number;
  flag?: string;
  setDraft: Dispatch<SetStateAction<string[]>>;
}) {
  const heading = row.kind === 'heading';
  const label = heading
    ? `${row.label} (${row.count}) · Select all${row.matches ? ' matches' : ''}`
    : row.value;
  return (
    <div
      className="picker-option"
      title={label}
      style={{ paddingLeft: 12 + row.depth * 16 }}
    >
      <Checkbox
        variant="secondary"
        isSelected={selected}
        isIndeterminate={indeterminate}
        aria-label={
          heading
            ? `Select all${row.matches ? ' matches' : ''} in ${row.group} (${row.count} options)`
            : row.value
        }
        aria-description={`Group ${row.group}`}
        onChange={(checked) =>
          setDraft((previous) =>
            row.kind === 'heading'
              ? toggleCategory(previous, row.values)
              : checked
                ? [...new Set([...previous, row.value])]
                : previous.filter((value) => value !== row.value),
          )
        }
      >
        <Checkbox.Content>
          <Checkbox.Control>
            <Checkbox.Indicator />
          </Checkbox.Control>
          {flag && (
            <img className="country-flag" src={flag} alt="" width={16} />
          )}
          <span
            className="picker-option-label"
            style={{
              maxWidth: width
                ? Math.max(0, width - 64 - row.depth * 16 - (flag ? 24 : 0))
                : undefined,
            }}
          >
            {label}
          </span>
        </Checkbox.Content>
      </Checkbox>
    </div>
  );
});

export const FilterOptionList = memo(function FilterOptionList({
  rows,
  label,
  flags,
  draft,
  setDraft,
}: {
  rows: readonly OptionRow[];
  label: string;
  flags?: ReadonlyMap<string, string>;
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
              selected={
                row.kind === 'heading'
                  ? categorySelection(row.values, draft).selected
                  : draft.includes(row.value)
              }
              indeterminate={
                row.kind === 'heading' &&
                categorySelection(row.values, draft).indeterminate
              }
              width={width}
              flag={row.kind === 'heading' ? undefined : flags?.get(row.value)}
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
