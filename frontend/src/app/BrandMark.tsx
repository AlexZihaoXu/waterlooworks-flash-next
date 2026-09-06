import { useEffect, useSyncExternalStore } from 'react';
import { animate, motion, useMotionValue } from 'framer-motion';
import { settleAngle } from '../lib/brandMotion';

const motionPreference = '(prefers-reduced-motion: reduce)';
function subscribeMotionPreference(update: () => void) {
  const media = window.matchMedia(motionPreference);
  media.addEventListener('change', update);
  return () => media.removeEventListener('change', update);
}
function readMotionPreference() {
  return window.matchMedia(motionPreference).matches;
}

/** User-requested custom brand animation; HeroUI controls are not restyled. */
export function BrandMark({ active }: { active: boolean }) {
  const reducedMotion = useSyncExternalStore(
    subscribeMotionPreference,
    readMotionPreference,
    () => true,
  );
  const outer = useMotionValue(0);
  const inner = useMotionValue(0);
  useEffect(() => {
    if (reducedMotion) {
      outer.set(0);
      inner.set(0);
      return;
    }
    if (active) {
      const outside = animate(outer, outer.get() + 360, {
        duration: 4.8,
        repeat: Infinity,
        ease: 'linear',
      });
      const inside = animate(inner, inner.get() - 360, {
        duration: 3.2,
        repeat: Infinity,
        ease: 'linear',
      });
      return () => {
        outside.stop();
        inside.stop();
      };
    }
    outer.set(settleAngle(outer.get()));
    inner.set(settleAngle(inner.get()));
    if (outer.get() === 0 && inner.get() === 0) return;
    const settle = { duration: 0.65, ease: 'easeOut' as const };
    const outside = animate(outer, 0, settle);
    const inside = animate(inner, 0, settle);
    return () => {
      outside.stop();
      inside.stop();
    };
  }, [active, reducedMotion, outer, inner]);
  return (
    <svg
      className="brand-icon"
      width={32}
      height={32}
      viewBox="0 0 48 48"
      fill="none"
      aria-hidden="true"
      focusable="false"
      data-animating={active && !reducedMotion}
    >
      <motion.g
        data-ring="outer"
        style={{
          rotate: outer,
          transformOrigin: '24px 24px',
          transformBox: 'view-box',
        }}
      >
        <use href="/brand.svg#outer-ring" />
      </motion.g>
      <motion.g
        data-ring="inner"
        style={{
          rotate: inner,
          transformOrigin: '24px 24px',
          transformBox: 'view-box',
        }}
      >
        <use href="/brand.svg#inner-ring" />
      </motion.g>
    </svg>
  );
}
