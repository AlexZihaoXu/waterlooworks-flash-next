import { readFileSync } from 'node:fs';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { useRef } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  MarkFiltersCard,
  PostingMarkChip,
  PostingMarkControls,
  PostingMarkMenu,
} from '../../src/app/PostingMarks';
import {
  LONG_PRESS_MS,
  useContextMenuGesture,
  type MenuPoint,
} from '../../src/lib/useContextMenuGesture';
import {
  defaultMarkFilters,
  type PostingMark,
} from '../../src/lib/postingMarks';

// Generic identifiers and placeholder labels only; no posting content.
const at = '2026-01-01T00:00:00+00:00';
const mark = (
  state: PostingMark['state'],
  viewed_at: string | null = null,
): PostingMark => ({ posting_id: 'a', state, viewed_at, updated_at: at });

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function MenuHarness({
  current,
  onState,
  onViewed,
  onClose = () => {},
}: {
  current?: PostingMark;
  onState: (id: string, state: PostingMark['state']) => void;
  onViewed: (id: string, viewed: boolean) => void;
  onClose?: () => void;
}) {
  const anchor = useRef<HTMLSpanElement>(null);
  return (
    <>
      <span ref={anchor} />
      <PostingMarkMenu
        target={{ id: 'a', label: 'Placeholder title' }}
        anchorRef={anchor}
        mark={current}
        onClose={onClose}
        onState={onState}
        onViewed={onViewed}
      />
    </>
  );
}

describe('posting mark context menu', () => {
  it('offers every state with the current one checked', async () => {
    const onState = vi.fn();
    render(
      <MenuHarness
        current={mark('applied', at)}
        onState={onState}
        onViewed={vi.fn()}
      />,
    );
    const menu = await screen.findByRole('menu', {
      name: 'Mark Placeholder title',
    });
    const applied = within(menu).getByRole('menuitemradio', {
      name: 'Applied',
    });
    expect(applied).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(
      within(menu).getByRole('menuitemradio', { name: 'Interested' }),
    );
    expect(onState).toHaveBeenCalledWith('a', 'interested');
  });

  it('clears the mark and toggles viewed from the action section', async () => {
    const onState = vi.fn();
    const onViewed = vi.fn();
    const { unmount } = render(
      <MenuHarness
        current={mark('interested', at)}
        onState={onState}
        onViewed={onViewed}
      />,
    );
    fireEvent.click(
      await screen.findByRole('menuitem', { name: 'Mark as not viewed' }),
    );
    expect(onViewed).toHaveBeenCalledWith('a', false);
    unmount();
    render(
      <MenuHarness
        current={mark('interested')}
        onState={onState}
        onViewed={onViewed}
      />,
    );
    fireEvent.click(
      await screen.findByRole('menuitem', { name: 'Clear mark' }),
    );
    expect(onState).toHaveBeenCalledWith('a', null);
    render(<MenuHarness onState={onState} onViewed={onViewed} />);
    expect(
      (await screen.findAllByRole('menuitem', { name: 'Clear mark' })).at(-1),
    ).toHaveAttribute('aria-disabled', 'true');
  });
});

function GestureHarness({
  onOpen,
  onNavigate,
}: {
  onOpen: (id: string, point: MenuPoint) => void;
  onNavigate: () => void;
}) {
  const gesture = useContextMenuGesture(onOpen);
  return (
    <div data-testid="item" {...gesture('a')}>
      <a
        href="#placeholder"
        onClick={(event) => {
          event.preventDefault();
          onNavigate();
        }}
      >
        Placeholder
      </a>
    </div>
  );
}

describe('context menu gesture', () => {
  it('opens at the pointer on right-click instead of the browser menu', () => {
    const onOpen = vi.fn();
    render(<GestureHarness onOpen={onOpen} onNavigate={() => {}} />);
    const event = new MouseEvent('contextmenu', {
      bubbles: true,
      cancelable: true,
      clientX: 40,
      clientY: 60,
    });
    act(() => {
      screen.getByText('Placeholder').dispatchEvent(event);
    });
    expect(event.defaultPrevented).toBe(true);
    expect(onOpen).toHaveBeenCalledWith('a', { x: 40, y: 60 });
  });

  it('opens on a touch long-press and swallows the click that follows', () => {
    vi.useFakeTimers();
    const onOpen = vi.fn();
    const onNavigate = vi.fn();
    render(<GestureHarness onOpen={onOpen} onNavigate={onNavigate} />);
    const link = screen.getByText('Placeholder');
    fireEvent.pointerDown(link, {
      pointerType: 'touch',
      clientX: 5,
      clientY: 6,
    });
    act(() => vi.advanceTimersByTime(LONG_PRESS_MS));
    expect(onOpen).toHaveBeenCalledWith('a', { x: 5, y: 6 });
    fireEvent.pointerUp(link, { pointerType: 'touch' });
    fireEvent.click(link);
    expect(onNavigate).not.toHaveBeenCalled();
    fireEvent.click(link);
    expect(onNavigate).toHaveBeenCalledOnce();
  });

  it('never swallows a later click when the long-press produced none', () => {
    vi.useFakeTimers();
    const onNavigate = vi.fn();
    render(<GestureHarness onOpen={vi.fn()} onNavigate={onNavigate} />);
    const link = screen.getByText('Placeholder');
    fireEvent.pointerDown(link, { pointerType: 'touch' });
    act(() => vi.advanceTimersByTime(LONG_PRESS_MS));
    fireEvent.pointerUp(link, { pointerType: 'touch' });
    // No click followed the long-press; a later mouse click must navigate.
    act(() => vi.advanceTimersByTime(1000));
    fireEvent.click(link);
    expect(onNavigate).toHaveBeenCalledOnce();
    fireEvent.pointerDown(link, { pointerType: 'touch' });
    act(() => vi.advanceTimersByTime(LONG_PRESS_MS));
    fireEvent.pointerDown(link, { pointerType: 'mouse' });
    fireEvent.click(link);
    expect(onNavigate).toHaveBeenCalledTimes(2);
  });

  it('ignores short taps, scrolling moves and mouse presses', () => {
    vi.useFakeTimers();
    const onOpen = vi.fn();
    render(<GestureHarness onOpen={onOpen} onNavigate={() => {}} />);
    const link = screen.getByText('Placeholder');
    fireEvent.pointerDown(link, { pointerType: 'touch', clientX: 0 });
    fireEvent.pointerUp(link, { pointerType: 'touch' });
    fireEvent.pointerDown(link, { pointerType: 'touch', clientY: 0 });
    fireEvent.pointerMove(link, { pointerType: 'touch', clientY: 30 });
    fireEvent.pointerDown(link, { pointerType: 'mouse' });
    act(() => vi.advanceTimersByTime(LONG_PRESS_MS * 2));
    expect(onOpen).not.toHaveBeenCalled();
  });
});

describe('detail mark controls', () => {
  it('sets, switches and clears the state with a single-select group', () => {
    const onState = vi.fn();
    const { rerender } = render(
      <PostingMarkControls
        mark={undefined}
        onState={onState}
        onViewed={vi.fn()}
      />,
    );
    const group = screen.getByRole('radiogroup', { name: 'Your mark' });
    fireEvent.click(within(group).getByRole('radio', { name: 'Interested' }));
    expect(onState).toHaveBeenLastCalledWith('interested');
    rerender(
      <PostingMarkControls
        mark={mark('interested')}
        onState={onState}
        onViewed={vi.fn()}
      />,
    );
    expect(screen.getByRole('radio', { name: 'Interested' })).toBeChecked();
    fireEvent.click(screen.getByRole('radio', { name: 'Interested' }));
    expect(onState).toHaveBeenLastCalledWith(null);
  });

  it('shows viewed as a pressed toggle that unmarks it', () => {
    const onViewed = vi.fn();
    render(
      <PostingMarkControls
        mark={mark(null, at)}
        onState={vi.fn()}
        onViewed={onViewed}
      />,
    );
    const viewed = screen.getByRole('button', { name: 'Viewed' });
    expect(viewed).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(viewed);
    expect(onViewed).toHaveBeenCalledWith(false);
  });

  it('disables every control while marks are unavailable', () => {
    render(
      <PostingMarkControls
        mark={undefined}
        isDisabled
        onState={vi.fn()}
        onViewed={vi.fn()}
      />,
    );
    for (const radio of screen.getAllByRole('radio'))
      expect(radio).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Viewed' })).toBeDisabled();
  });
});

describe('mark chip', () => {
  it('labels marked states and renders nothing when unmarked', () => {
    const { container, rerender } = render(<PostingMarkChip state={null} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<PostingMarkChip state="not_interested" />);
    expect(screen.getByText('Not interested')).toBeInTheDocument();
  });
});

describe('mark filters card', () => {
  it('hides not-interested postings by default and reports changes', () => {
    const onChange = vi.fn();
    render(
      <MarkFiltersCard
        filters={defaultMarkFilters}
        onChange={onChange}
        status="ready"
        hiddenCount={2}
      />,
    );
    const hide = screen.getByRole('switch', { name: /Hide not interested/ });
    expect(hide).toBeChecked();
    expect(screen.getByText(/2 hidden/)).toBeInTheDocument();
    fireEvent.click(hide);
    expect(onChange).toHaveBeenLastCalledWith({
      ...defaultMarkFilters,
      hideNotInterested: false,
    });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Applied' }));
    expect(onChange).toHaveBeenLastCalledWith({
      ...defaultMarkFilters,
      states: ['applied'],
    });
    fireEvent.click(screen.getByRole('radio', { name: 'Not viewed' }));
    expect(onChange).toHaveBeenLastCalledWith({
      ...defaultMarkFilters,
      viewed: 'unviewed',
    });
  });

  it('disables the hide switch while not interested is explicitly selected', () => {
    render(
      <MarkFiltersCard
        filters={{ ...defaultMarkFilters, states: ['not_interested'] }}
        onChange={vi.fn()}
        status="ready"
        hiddenCount={0}
      />,
    );
    expect(
      screen.getByRole('switch', { name: /Hide not interested/ }),
    ).toBeDisabled();
  });

  it('explains unavailable marks instead of filtering on missing data', () => {
    render(
      <MarkFiltersCard
        filters={defaultMarkFilters}
        onChange={vi.fn()}
        status="error"
        hiddenCount={0}
      />,
    );
    expect(screen.getByText(/Marks could not be loaded/)).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Applied' })).toBeDisabled();
  });
});

describe('workspace wiring', () => {
  const app = readFileSync('src/app/App.tsx', 'utf8');
  const styles = readFileSync('src/styles/index.css', 'utf8');

  it('filters by marks, marks opened postings viewed and offers the menu', () => {
    expect(app).toContain('filterByMarks(');
    expect(app).toContain('marks.markViewed(');
    expect(app).toContain('<PostingMarkMenu');
    expect(app).toContain('<PostingMarkControls');
    expect(app).toContain('<MarkFiltersCard');
    expect(app).toContain('<PostingMarkChip');
  });

  it('dims viewed postings on the native wrapper only', () => {
    expect(app).toMatch(/className="posting-item"[^>]*data-viewed=/s);
    const rule = /\.posting-item\[data-viewed='true'\]\s*\{([^}]*)\}/.exec(
      styles,
    );
    expect(rule?.[1]).toMatch(/opacity:/);
    expect(styles).not.toMatch(/\.posting-item[^{]*\.(card|chip|link)\b/);
  });
});
