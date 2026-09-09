import { useEffect, useState } from 'react';
import { fetchJobDetail, type JobDetailView } from './jobDetail';
export type DetailState =
  | { id: string; status: 'loading' | 'error' | 'not-found' }
  | { id: string; status: 'ready'; data: JobDetailView };
export function useJobDetail(
  id: string | null,
  revision = '',
): DetailState | null {
  const [state, setState] = useState<DetailState | null>(null);
  useEffect(() => {
    if (id === null) return;
    const controller = new AbortController();
    let active = true;
    void fetchJobDetail(id, controller.signal)
      .then((data) => {
        if (active)
          setState(
            data === null
              ? { id, status: 'not-found' }
              : { id, status: 'ready', data },
          );
      })
      .catch(() => {
        if (active) setState({ id, status: 'error' });
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [id, revision]);
  return id === null
    ? null
    : state?.id === id
      ? state
      : { id, status: 'loading' };
}
