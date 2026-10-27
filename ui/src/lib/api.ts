/**
 * Typed admin-API client — the app's single I/O boundary (UI-M2 design (b)).
 *
 * Contract facts (verified against caldav_mcp/admin.py and docs/api.md):
 * - Relative URLs only (`/admin/…`) — same-origin in production and through
 *   the dev proxy; a unit test asserts every issued fetch stays relative.
 * - The admin key rides in `Authorization: Bearer …` on every call, read
 *   from the sessionStorage-backed auth module. The server also accepts
 *   `X-Api-Key`; one header, one code path.
 * - Errors are always `{"error": "<message>"}` JSON from the API, EXCEPT
 *   when the admin API is not registered (not pro mode / no admin key),
 *   where the framework serves its own plain-text 404 — the disambiguation
 *   signal for the `disabled` kind (fixed decision (d)).
 * - Bodies are never logged: create/rotate request and response bodies can
 *   carry key material, so nothing is ever console-logged (eslint guard).
 */

import { loadKey } from './auth';

export type ApiErrorKind =
  | 'validation' // 400 — invalid input, show the server message inline
  | 'auth' // 401 — missing/invalid admin key
  | 'notfound' // 404 with a JSON body — unknown user or config
  | 'conflict' // 409 — duplicate user / FK race
  | 'ratelimited' // 429 — Retry-After header parsed into retryAfterSeconds
  | 'unavailable' // 503 — store down, message carries the retry hint
  | 'server' // 500 — unexpected server error, generic message
  | 'disabled' // 404 with a non-JSON body — admin API not enabled here
  | 'network'; // fetch rejected — server unreachable

export class ApiError extends Error {
  readonly status: number;
  readonly kind: ApiErrorKind;
  readonly retryAfterSeconds?: number;

  constructor(status: number, kind: ApiErrorKind, message: string, retryAfterSeconds?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.kind = kind;
    if (retryAfterSeconds !== undefined) this.retryAfterSeconds = retryAfterSeconds;
  }
}

/** User payload shape — the API never returns keys or key hashes. */
export interface User {
  username: string;
  configs: string[];
}

export interface DeleteUserResult {
  deleted: boolean;
  username: string;
}

export interface RevokeConfigResult {
  revoked: boolean;
  username: string;
  config: string;
}

export interface RotateKeyResult {
  username: string;
  rotated: boolean;
}

const KIND_BY_STATUS: Record<number, ApiErrorKind> = {
  400: 'validation',
  401: 'auth',
  409: 'conflict',
  429: 'ratelimited',
  500: 'server',
  503: 'unavailable',
};

const DEFAULT_MESSAGES: Record<number, string> = {
  400: 'invalid request',
  401: 'unauthorized — missing or invalid credentials',
  409: 'conflict',
  429: 'rate limited',
  500: 'internal server error',
  503: 'config store unavailable — retry shortly',
};

function kindFor(status: number): ApiErrorKind {
  const mapped = KIND_BY_STATUS[status];
  if (mapped !== undefined) return mapped;
  // Unlisted 5xx → server; unlisted 4xx → validation (route-specific 4xx
  // beyond the documented table are all "bad input" from the UI's view).
  return status >= 500 ? 'server' : 'validation';
}

function parseRetryAfter(raw: string | null): number | undefined {
  if (raw === null) return undefined;
  const trimmed = raw.trim();
  if (!/^\d+$/.test(trimmed)) return undefined;
  return Number.parseInt(trimmed, 10);
}

async function toApiError(response: Response): Promise<ApiError> {
  const text = await response.text();
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    parsed = undefined;
  }
  const isJsonObject =
    typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed);
  const serverMessage =
    isJsonObject && typeof (parsed as { error?: unknown }).error === 'string'
      ? (parsed as { error: string }).error
      : undefined;

  if (response.status === 404) {
    // Fixed decision (d): JSON `{"error": …}` → the route exists, the
    // subject is unknown. Non-JSON (framework plain text) → the admin API
    // was never registered on this server. `disabled` must never be
    // rendered as "wrong key".
    return isJsonObject
      ? new ApiError(404, 'notfound', serverMessage ?? 'not found')
      : new ApiError(
          404,
          'disabled',
          'the admin API is not available on this server — pro mode with CALDAV_MCP_ADMIN_KEY is required',
        );
  }

  const kind = kindFor(response.status);
  const retryAfterSeconds =
    kind === 'ratelimited' ? parseRetryAfter(response.headers.get('Retry-After')) : undefined;
  const message =
    serverMessage ??
    DEFAULT_MESSAGES[response.status] ??
    `request failed (HTTP ${response.status})`;
  return new ApiError(response.status, kind, message, retryAfterSeconds);
}

interface RequestOptions {
  method?: string;
  body?: unknown;
}

/**
 * Issue one request against the admin API. Relative paths only — the
 * invariant is asserted here and pinned by a unit test, so no future
 * endpoint can accidentally embed an origin (or a key) in a URL.
 */
async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  if (!path.startsWith('/') || path.startsWith('//')) {
    throw new Error('admin API paths must be origin-relative');
  }
  const headers: Record<string, string> = { Accept: 'application/json' };
  const key = loadKey();
  if (key !== null && key !== '') headers.Authorization = `Bearer ${key}`;
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';

  let response: Response;
  try {
    response = await fetch(path, {
      method: options.method ?? 'GET',
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    // Network-level failure. Never log details (could carry context).
    throw new ApiError(
      0,
      'network',
      'the server is unreachable — check the connection and try again',
    );
  }
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

// ---------------------------------------------------------------------------
// Route groups (six: users read, user read, configs read, user writes,
// key rotation, per-user config management)
// ---------------------------------------------------------------------------

export async function listUsers(): Promise<User[]> {
  const data = await request<{ users: User[] }>('/admin/users');
  return data.users;
}

export function getUser(username: string): Promise<User> {
  return request<User>(`/admin/users/${encodeURIComponent(username)}`);
}

export async function listConfigs(): Promise<string[]> {
  const data = await request<{ configs: string[] }>('/admin/configs');
  return data.configs;
}

export function createUser(username: string, key: string): Promise<User> {
  return request<User>('/admin/users', { method: 'POST', body: { username, key } });
}

export function deleteUser(username: string): Promise<DeleteUserResult> {
  return request<DeleteUserResult>(`/admin/users/${encodeURIComponent(username)}`, {
    method: 'DELETE',
  });
}

export function grantConfig(username: string, config: string): Promise<User> {
  return request<User>(`/admin/users/${encodeURIComponent(username)}/configs`, {
    method: 'POST',
    body: { config },
  });
}

export function replaceConfigs(username: string, configs: string[]): Promise<User> {
  return request<User>(`/admin/users/${encodeURIComponent(username)}/configs`, {
    method: 'PUT',
    body: { configs },
  });
}

export function revokeConfig(username: string, config: string): Promise<RevokeConfigResult> {
  return request<RevokeConfigResult>(
    `/admin/users/${encodeURIComponent(username)}/configs/${encodeURIComponent(config)}`,
    { method: 'DELETE' },
  );
}

export function rotateKey(username: string, key: string): Promise<RotateKeyResult> {
  return request<RotateKeyResult>(`/admin/users/${encodeURIComponent(username)}/key`, {
    method: 'PUT',
    body: { key },
  });
}

// ---------------------------------------------------------------------------
// Success-signal surfacing (design (b))
// ---------------------------------------------------------------------------

export interface MutationSignals {
  /** `"reload": "failed"` — the write stands but live-apply failed. */
  reloadFailed: boolean;
  /**
   * A restart is needed: `config_topology_restart_required: true` (emitted
   * by grant/replace for configs outside the started topology), or the
   * legacy `restart_required: true` mapped defensively — no route has
   * emitted `restart_required` since M5 (docs/api.md, "Restart and
   * reload"), but the branch keeps the restart banner covered if the
   * field ever reappears.
   */
  restartRequired: boolean;
}

export function mutationSignals(body: unknown): MutationSignals {
  if (typeof body !== 'object' || body === null) {
    return { reloadFailed: false, restartRequired: false };
  }
  const record = body as Record<string, unknown>;
  return {
    reloadFailed: record.reload === 'failed',
    restartRequired:
      record.config_topology_restart_required === true || record.restart_required === true,
  };
}
