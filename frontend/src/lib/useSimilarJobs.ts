import { useEffect, useRef, useState } from 'react';
import type { DetailState } from './useJobDetail';
import type {
  SimilarJobsState,
  SimilarRequest,
  SimilarResponse,
} from './similarJobs';

export function useSimilarJobs(
  detail: DetailState | null,
  revision: string,
): SimilarJobsState {
  const worker = useRef<Worker | null>(null);
  const sequence = useRef(0);
  const [saved, setSaved] = useState<{
    key: string;
    state: SimilarJobsState;
  } | null>(null);
  const id =
    detail?.status === 'ready' && detail.data.content ? detail.id : null;
  const text =
    detail?.status === 'ready' && detail.data.content
      ? Object.values(detail.data.content.fields).join('\n\n')
      : '';
  const key = JSON.stringify([id, revision, text]);
  useEffect(() => {
    if (id === null) return;
    let active = true;
    const requestId = (sequence.current += 2);
    const fail = () => {
      if (active) setSaved({ key, state: { status: 'error' } });
    };
    try {
      worker.current ??= new Worker(
        new URL('./similarJobs.worker.ts', import.meta.url),
        { type: 'module' },
      );
      const instance = worker.current;
      instance.onmessage = (event: MessageEvent<SimilarResponse>) => {
        if (active && event.data.requestId === requestId)
          setSaved({ key, state: event.data.state });
      };
      instance.onerror = (event) => {
        event.preventDefault();
        instance.terminate();
        worker.current = null;
        fail();
      };
      instance.postMessage({
        requestId,
        sourceId: id,
        text,
      } satisfies SimilarRequest);
      return () => {
        active = false;
        instance.postMessage({
          requestId: requestId + 1,
          cancel: true,
        } satisfies SimilarRequest);
      };
    } catch {
      void Promise.resolve().then(fail);
      return () => {
        active = false;
      };
    }
  }, [id, key, text]);
  useEffect(
    () => () => {
      worker.current?.terminate();
      worker.current = null;
    },
    [],
  );
  return id === null
    ? { status: 'idle' }
    : saved?.key === key
      ? saved.state
      : { status: 'loading' };
}
