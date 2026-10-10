import dns from 'node:dns';
import net from 'node:net';

export class NetworkBlocked extends Error {
  constructor() {
    super('network_blocked');
    this.name = 'NetworkBlocked';
  }
}

export const MAX_RESPONSE_BYTES = 16 * 1024 * 1024;

type LookupCallback = (
  error: NodeJS.ErrnoException | null,
  address?: string | dns.LookupAddress[],
  family?: number,
) => void;

/**
 * Replace process-wide DNS resolution. Named hosts outside `allowed` fail with
 * ENOTFOUND; a pinned host resolves only to the backend-validated address, so
 * a rebinding answer can never redirect a request elsewhere.
 */
export function installDnsPolicy(
  allowed: ReadonlySet<string>,
  pinned: ReadonlyMap<string, string>,
): void {
  const original = dns.lookup.bind(dns) as (
    hostname: string,
    options: dns.LookupOptions,
    callback: LookupCallback,
  ) => void;
  const replacement = (
    hostname: string,
    options: dns.LookupOptions | number | LookupCallback,
    callback?: LookupCallback,
  ): void => {
    const done = (typeof options === 'function' ? options : callback)!;
    const opts: dns.LookupOptions =
      typeof options === 'object' && options !== null
        ? options
        : typeof options === 'number'
          ? { family: options }
          : {};
    const host = hostname.toLowerCase();
    const address = pinned.get(host);
    if (address !== undefined) {
      const family = net.isIPv6(address) ? 6 : 4;
      if (opts.all) process.nextTick(done, null, [{ address, family }]);
      else process.nextTick(done, null, address, family);
      return;
    }
    if (!allowed.has(host)) {
      const error: NodeJS.ErrnoException = new Error('ENOTFOUND');
      error.code = 'ENOTFOUND';
      process.nextTick(done, error);
      return;
    }
    original(hostname, opts, done);
  };
  (dns as unknown as { lookup: unknown }).lookup = replacement;
  (dns.promises as unknown as { lookup: unknown }).lookup = () =>
    Promise.reject(new NetworkBlocked());
}

function requestUrl(input: string | URL | Request): URL {
  if (input instanceof URL) return input;
  if (typeof input === 'string') return new URL(input);
  return new URL(input.url);
}

function capped(body: ReadableStream<Uint8Array>, limit: number) {
  let seen = 0;
  return body.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        seen += chunk.byteLength;
        if (seen > limit) controller.error(new NetworkBlocked());
        else controller.enqueue(chunk);
      },
    }),
  );
}

/**
 * fetch that only reaches exact allowlisted origins, never follows redirects
 * and bounds response bodies. Installed globally and passed explicitly to
 * pi-ai so OAuth refresh and model requests share the same policy.
 */
export function guardedFetch(
  allowedOrigins: ReadonlySet<string>,
  base: typeof fetch = globalThis.fetch,
  limit = MAX_RESPONSE_BYTES,
): typeof fetch {
  return async (input, init) => {
    let url: URL;
    try {
      url = requestUrl(input as string | URL | Request);
    } catch {
      throw new NetworkBlocked();
    }
    if (!allowedOrigins.has(url.origin) || url.username || url.password)
      throw new NetworkBlocked();
    const request =
      input instanceof Request
        ? new Request(input, { ...init, redirect: 'error' })
        : null;
    const response = request
      ? await base(request)
      : await base(input as string | URL, { ...init, redirect: 'error' });
    const declared = response.headers.get('content-length');
    if (declared !== null && Number(declared) > limit) {
      await response.body?.cancel().catch(() => undefined);
      throw new NetworkBlocked();
    }
    if (!response.body) return response;
    return new Response(capped(response.body, limit), {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  };
}
