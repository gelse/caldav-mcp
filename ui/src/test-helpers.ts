import { vi } from 'vitest';

/** JSON API response (success or `{"error": …}` failure). */
export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });
}

/** The framework's plain-text 404 served when the admin API is disabled. */
export function plainTextNotFound(body = 'Not Found'): Response {
  return new Response(body, {
    status: 404,
    headers: { 'Content-Type': 'text/plain' },
  });
}

export type FetchHandler = (input: RequestInfo | URL, init?: RequestInit) => Response | Promise<Response>;

/**
 * Stub `fetch` with a queue of responses (last one repeats — static
 * Response objects are cloned per call since a body can only be read
 * once). Returns the mock so tests can assert call counts and arguments.
 */
export function installFetch(
  ...responses: Array<Response | FetchHandler>
): ReturnType<typeof vi.fn> {
  const queue = responses.map((entry) =>
    typeof entry === 'function' ? entry : (): Response => entry.clone(),
  );
  const mock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const next = queue.length > 1 ? queue.shift() : queue[0];
    if (next === undefined) throw new Error('installFetch: no response queued');
    return next(input, init);
  });
  vi.stubGlobal('fetch', mock);
  return mock;
}

/** Every recorded fetch URL — used by the relative-URL assertions. */
export function fetchUrls(mock: ReturnType<typeof vi.fn>): string[] {
  return mock.mock.calls.map((call) => String(call[0]));
}
