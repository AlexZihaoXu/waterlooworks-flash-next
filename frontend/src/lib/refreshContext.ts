import { createContext } from 'react';
import type { RefreshSnapshot } from './workspace';

// Live refresh snapshots change about once a second during a refresh. Only the
// refresh presentation subscribes to them; the memoized browser receives
// primitive summaries so the posting grid does not re-render on every tick.
export const RefreshContext = createContext<RefreshSnapshot | null>(null);
