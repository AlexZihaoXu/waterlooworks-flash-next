import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  type MouseEvent,
  type PointerEvent,
} from 'react';

export type MenuPoint = { x: number; y: number };
export const LONG_PRESS_MS = 500;
const MOVE_TOLERANCE_PX = 10;
/** Only the click ending the long-press itself is swallowed, never a later one. */
const SWALLOW_WINDOW_MS = 400;

type Press = {
  x: number;
  y: number;
  timer: ReturnType<typeof setTimeout>;
};

/**
 * Context-menu interaction for a native list item: right-click (or the
 * keyboard context-menu key) and a touch long-press both open the caller's
 * menu at the pointer. Short taps, scroll moves and mouse presses keep their
 * normal behavior; the click that ends a long-press never navigates.
 */
export function useContextMenuGesture(
  open: (id: string, point: MenuPoint) => void,
) {
  const latestOpen = useRef(open);
  useLayoutEffect(() => {
    latestOpen.current = open;
  });
  const press = useRef<Press | null>(null);
  const swallowUntil = useRef(0);
  const cancel = useCallback(() => {
    if (press.current) clearTimeout(press.current.timer);
    press.current = null;
  }, []);
  useEffect(() => cancel, [cancel]);

  return useCallback(
    (id: string) => ({
      onContextMenu: (event: MouseEvent<HTMLElement>) => {
        event.preventDefault();
        if (press.current)
          swallowUntil.current = Date.now() + SWALLOW_WINDOW_MS;
        cancel();
        // Keyboard-invoked menus report no pointer: anchor to the target.
        const keyboard = event.clientX === 0 && event.clientY === 0;
        const rect =
          event.target instanceof Element
            ? event.target.getBoundingClientRect()
            : null;
        latestOpen.current(
          id,
          keyboard && rect
            ? { x: rect.left, y: rect.bottom }
            : { x: event.clientX, y: event.clientY },
        );
      },
      onPointerDown: (event: PointerEvent<HTMLElement>) => {
        swallowUntil.current = 0;
        if (event.pointerType !== 'touch' && event.pointerType !== 'pen')
          return;
        cancel();
        const x = event.clientX;
        const y = event.clientY;
        press.current = {
          x,
          y,
          timer: setTimeout(() => {
            press.current = null;
            // Held: swallow until shortly after the finger lifts, wherever
            // it lifts (the menu may now be under it).
            swallowUntil.current = Number.POSITIVE_INFINITY;
            const release = () => {
              if (swallowUntil.current === Number.POSITIVE_INFINITY)
                swallowUntil.current = Date.now() + SWALLOW_WINDOW_MS;
            };
            window.addEventListener('pointerup', release, {
              once: true,
              capture: true,
            });
            window.addEventListener('pointercancel', release, {
              once: true,
              capture: true,
            });
            if ('vibrate' in navigator) navigator.vibrate?.(10);
            latestOpen.current(id, { x, y });
          }, LONG_PRESS_MS),
        };
      },
      onPointerMove: (event: PointerEvent<HTMLElement>) => {
        const current = press.current;
        if (
          current &&
          Math.hypot(event.clientX - current.x, event.clientY - current.y) >
            MOVE_TOLERANCE_PX
        )
          cancel();
      },
      onPointerUp: cancel,
      onPointerCancel: cancel,
      onClickCapture: (event: MouseEvent<HTMLElement>) => {
        if (Date.now() >= swallowUntil.current) return;
        swallowUntil.current = 0;
        event.preventDefault();
        event.stopPropagation();
      },
    }),
    [cancel],
  );
}
