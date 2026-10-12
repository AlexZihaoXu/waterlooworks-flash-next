import type { RefObject } from 'react';
import type { IconType } from 'react-icons';
import {
  LuBookmark,
  LuEraser,
  LuEye,
  LuEyeOff,
  LuSend,
  LuStar,
  LuThumbsDown,
} from 'react-icons/lu';
import {
  Card,
  Checkbox,
  CheckboxGroup,
  Chip,
  Description,
  Dropdown,
  Header,
  Label,
  Radio,
  RadioGroup,
  Separator,
  Switch,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@heroui/react';
import {
  MARK_STATES,
  isMarkState,
  isMarkStateFilter,
  markLabels,
  markStateFilterLabels,
  viewedFilterLabels,
  type MarkFilters,
  type MarkState,
  type MarkStateFilter,
  type PostingMark,
  type ViewedFilter,
} from '../lib/postingMarks';
import type { MarksStatus } from '../lib/usePostingMarks';

const markPresentation: Record<
  MarkState,
  { icon: IconType; color: 'accent' | 'success' | 'default' }
> = {
  interested: { icon: LuStar, color: 'accent' },
  applied: { icon: LuSend, color: 'success' },
  not_interested: { icon: LuThumbsDown, color: 'default' },
};
const STATE_FILTERS: readonly MarkStateFilter[] = [...MARK_STATES, 'none'];
const VIEWED_FILTERS: readonly ViewedFilter[] = ['any', 'viewed', 'unviewed'];

/** Documented small soft HeroUI Chip with a leading icon; null when unmarked. */
export function PostingMarkChip({ state }: { state: MarkState | null }) {
  if (!state) return null;
  const { icon: Icon, color } = markPresentation[state];
  return (
    <Chip size="sm" variant="soft" color={color}>
      <Icon size={12} aria-hidden="true" />
      <Chip.Label>{markLabels[state]}</Chip.Label>
    </Chip>
  );
}

export type MarkMenuTarget = { id: string; label: string };

/**
 * Context menu: the documented controlled Dropdown (no visible trigger),
 * positioned by `triggerRef` against a native anchor at the pointer. The
 * state section is a single selection; choosing the checked state clears it.
 */
export function PostingMarkMenu({
  target,
  anchorRef,
  mark,
  onClose,
  onState,
  onViewed,
}: {
  target: MarkMenuTarget | null;
  anchorRef: RefObject<HTMLElement | null>;
  mark: PostingMark | undefined;
  onClose: () => void;
  onState: (id: string, state: MarkState | null) => void;
  onViewed: (id: string, viewed: boolean) => void;
}) {
  const viewed = Boolean(mark?.viewed_at);
  return (
    <Dropdown
      isOpen={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Dropdown.Popover triggerRef={anchorRef} placement="bottom start">
        <Dropdown.Menu
          aria-label={`Mark ${target?.label ?? 'posting'}`}
          disabledKeys={mark?.state ? [] : ['clear']}
          onAction={(key) => {
            if (!target) return;
            if (key === 'clear') onState(target.id, null);
            if (key === 'viewed') onViewed(target.id, !viewed);
          }}
        >
          <Dropdown.Section
            selectionMode="single"
            selectedKeys={mark?.state ? [mark.state] : []}
            onSelectionChange={(keys) => {
              if (!target || keys === 'all') return;
              const [key] = keys;
              onState(target.id, isMarkState(key) ? key : null);
            }}
          >
            <Header>Mark as</Header>
            {MARK_STATES.map((state) => {
              const Icon = markPresentation[state].icon;
              return (
                <Dropdown.Item
                  key={state}
                  id={state}
                  textValue={markLabels[state]}
                >
                  <Dropdown.ItemIndicator />
                  <Icon aria-hidden="true" />
                  <Label>{markLabels[state]}</Label>
                </Dropdown.Item>
              );
            })}
          </Dropdown.Section>
          <Separator />
          <Dropdown.Section>
            <Dropdown.Item id="clear" textValue="Clear mark">
              <LuEraser aria-hidden="true" />
              <Label>Clear mark</Label>
            </Dropdown.Item>
            <Dropdown.Item
              id="viewed"
              textValue={viewed ? 'Mark as not viewed' : 'Mark as viewed'}
            >
              {viewed ? (
                <LuEyeOff aria-hidden="true" />
              ) : (
                <LuEye aria-hidden="true" />
              )}
              <Label>{viewed ? 'Mark as not viewed' : 'Mark as viewed'}</Label>
            </Dropdown.Item>
          </Dropdown.Section>
        </Dropdown.Menu>
      </Dropdown.Popover>
    </Dropdown>
  );
}

/**
 * Visible, keyboard-reachable equivalent of the context menu for the open
 * posting: a documented single-select ToggleButtonGroup (pressing the
 * selected state clears it) plus a Viewed ToggleButton.
 */
export function PostingMarkControls({
  mark,
  isDisabled = false,
  compact = false,
  onState,
  onViewed,
}: {
  mark: PostingMark | undefined;
  isDisabled?: boolean;
  compact?: boolean;
  onState: (state: MarkState | null) => void;
  onViewed: (viewed: boolean) => void;
}) {
  return (
    <div className="mark-controls">
      <ToggleButtonGroup
        aria-label="Your mark"
        selectionMode="single"
        size="sm"
        fullWidth={compact}
        isDisabled={isDisabled}
        selectedKeys={mark?.state ? [mark.state] : []}
        onSelectionChange={(keys) => {
          const [key] = keys;
          onState(isMarkState(key) ? key : null);
        }}
      >
        {MARK_STATES.map((state, index) => {
          const Icon = markPresentation[state].icon;
          return (
            <ToggleButton key={state} id={state}>
              {index > 0 && <ToggleButtonGroup.Separator />}
              {!compact && <Icon aria-hidden="true" />}
              {markLabels[state]}
            </ToggleButton>
          );
        })}
      </ToggleButtonGroup>
      <ToggleButton
        size="sm"
        isDisabled={isDisabled}
        isSelected={Boolean(mark?.viewed_at)}
        onChange={onViewed}
      >
        <LuEye aria-hidden="true" />
        Viewed
      </ToggleButton>
    </div>
  );
}

/** Documented Card/CheckboxGroup/RadioGroup/Switch composition. */
export function MarkFiltersCard({
  filters,
  onChange,
  status,
  hiddenCount,
  isDisabled = false,
}: {
  filters: MarkFilters;
  onChange: (filters: MarkFilters) => void;
  status: MarksStatus;
  hiddenCount: number;
  isDisabled?: boolean;
}) {
  const disabled = isDisabled || status !== 'ready';
  const explicit = filters.states.includes('not_interested');
  return (
    <Card>
      <Card.Header>
        <div className="filter-heading">
          <LuBookmark aria-hidden="true" />
          <Typography type="h6" role="heading" aria-level={2}>
            Your marks
          </Typography>
        </div>
      </Card.Header>
      <Separator />
      <Card.Content>
        <div className="stack">
          <CheckboxGroup
            value={[...filters.states]}
            isDisabled={disabled}
            onChange={(value) =>
              onChange({ ...filters, states: value.filter(isMarkStateFilter) })
            }
          >
            <Label>Mark</Label>
            {STATE_FILTERS.map((state) => (
              <Checkbox key={state} value={state} variant="secondary">
                <Checkbox.Content>
                  <Checkbox.Control>
                    <Checkbox.Indicator />
                  </Checkbox.Control>
                  {markStateFilterLabels[state]}
                </Checkbox.Content>
              </Checkbox>
            ))}
          </CheckboxGroup>
          <RadioGroup
            variant="secondary"
            value={filters.viewed}
            isDisabled={disabled}
            onChange={(value) => {
              if (VIEWED_FILTERS.includes(value as ViewedFilter))
                onChange({ ...filters, viewed: value as ViewedFilter });
            }}
          >
            <Label>Viewed</Label>
            {VIEWED_FILTERS.map((value) => (
              <Radio key={value} value={value}>
                <Radio.Content>
                  <Radio.Control>
                    <Radio.Indicator />
                  </Radio.Control>
                  {viewedFilterLabels[value]}
                </Radio.Content>
              </Radio>
            ))}
          </RadioGroup>
          <Switch
            isSelected={filters.hideNotInterested && !explicit}
            isDisabled={disabled || explicit}
            onChange={(hideNotInterested) =>
              onChange({ ...filters, hideNotInterested })
            }
          >
            <Switch.Content>
              <Switch.Control>
                <Switch.Thumb />
              </Switch.Control>
              Hide not interested
            </Switch.Content>
            {hiddenCount > 0 && (
              <Description>
                {hiddenCount.toLocaleString('en-CA')} hidden
              </Description>
            )}
          </Switch>
          <Typography type="body-xs" color="muted">
            {status === 'error'
              ? 'Marks could not be loaded. Reload the page to try again.'
              : 'Right-click a posting (long-press on touch) to mark it. Opening a posting marks it viewed.'}
          </Typography>
        </div>
      </Card.Content>
    </Card>
  );
}
