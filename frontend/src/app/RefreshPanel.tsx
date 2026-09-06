import type { ReactNode } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';

/** Animate native layout, including the space contributed to the workspace. */
export function RefreshPanel({
  visible,
  children,
}: {
  visible: boolean;
  children: ReactNode;
}) {
  const reducedMotion = useReducedMotion();
  return (
    <AnimatePresence initial={false}>
      {visible && (
        <motion.div
          className="refresh-panel"
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: reducedMotion ? 0 : 0.28, ease: 'easeInOut' }}
        >
          <div className="refresh-panel-content">{children}</div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
