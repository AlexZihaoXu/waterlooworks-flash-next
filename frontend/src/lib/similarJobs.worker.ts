import {
  buildSimilarSearch,
  parseKeywordIndex,
  rankSimilarJobs,
  type SimilarRequest,
  type SimilarResponse,
  type SimilarSearch,
} from './similarJobs';

let corpus: SimilarSearch | null = null;
let etag: string | null = null;
let active: AbortController | null = null;
let requestId = 0;

self.onmessage = (event: MessageEvent<SimilarRequest>) => {
  const request = event.data;
  active?.abort();
  requestId = request.requestId;
  if ('cancel' in request) return;
  const controller = new AbortController();
  active = controller;
  const timeout = setTimeout(() => controller.abort(), 30000);
  const send = (state: SimilarResponse['state']) => {
    if (!controller.signal.aborted && requestId === request.requestId)
      self.postMessage({
        requestId: request.requestId,
        state,
      } satisfies SimilarResponse);
  };
  void (async () => {
    // Revalidate on each selected posting/revision, not via a polling loop.
    const response = await fetch('/api/keyword-index', {
      cache: 'no-store',
      signal: controller.signal,
      headers: etag ? { 'If-None-Match': etag } : {},
    });
    if (response.status !== 304 || !corpus) {
      if (
        !response.ok ||
        !response.headers.get('content-type')?.includes('application/json')
      )
        throw new Error('Index unavailable');
      const index = parseKeywordIndex(await response.json());
      if (controller.signal.aborted) return;
      corpus = buildSimilarSearch(index);
      etag = response.headers.get('etag');
    }
    if (!controller.signal.aborted && corpus)
      send({
        status: 'ready',
        result: rankSimilarJobs(request.text, request.sourceId, corpus),
      });
  })()
    .catch(() => {
      // Includes timeout: never expose exception text or show stale cached results.
      if (requestId === request.requestId)
        self.postMessage({
          requestId: request.requestId,
          state: { status: 'error' },
        } satisfies SimilarResponse);
    })
    .finally(() => clearTimeout(timeout));
};
