import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  createUser,
  deleteUser,
  getUser,
  grantConfig,
  listConfigs,
  listUsers,
  mutationSignals,
  replaceConfigs,
  revokeConfig,
  rotateKey,
} from './api';
import { saveKey } from './auth';
import { fetchUrls, installFetch, jsonResponse, plainTextNotFound } from '../test-helpers';

async function captureError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error('expected the call to reject with ApiError');
}

describe('status → kind mapping', () => {
  it('maps 400 to validation and shows the server message', async () => {
    installFetch(jsonResponse({ error: 'key must be a non-empty string' }, 400));
    const error = await captureError(createUser('alice', ''));
    expect(error.status).toBe(400);
    expect(error.kind).toBe('validation');
    expect(error.message).toBe('key must be a non-empty string');
  });

  it('maps 401 to auth', async () => {
    installFetch(jsonResponse({ error: 'unauthorized - missing or invalid credentials' }, 401));
    const error = await captureError(listUsers());
    expect(error.status).toBe(401);
    expect(error.kind).toBe('auth');
    expect(error.message).toContain('unauthorized');
  });

  it('maps 404 with a JSON body to notfound (unknown user/config)', async () => {
    installFetch(jsonResponse({ error: "user not found: 'ghost'" }, 404));
    const error = await captureError(getUser('ghost'));
    expect(error.status).toBe(404);
    expect(error.kind).toBe('notfound');
    expect(error.message).toBe("user not found: 'ghost'");
  });

  it('maps 404 with a plain-text body to disabled (admin API not enabled)', async () => {
    installFetch(plainTextNotFound('Not Found'));
    const error = await captureError(listUsers());
    expect(error.status).toBe(404);
    expect(error.kind).toBe('disabled');
    expect(error.message).toContain('not available');
    // Never rendered as a key problem.
    expect(error.message).not.toContain('invalid');
  });

  it('maps 404 HTML error pages to disabled as well (non-JSON body)', async () => {
    installFetch(
      new Response('<html><body>404 Not Found</body></html>', {
        status: 404,
        headers: { 'Content-Type': 'text/html' },
      }),
    );
    const error = await captureError(listConfigs());
    expect(error.kind).toBe('disabled');
  });

  it('maps 409 to conflict', async () => {
    installFetch(jsonResponse({ error: "user already exists: 'alice'" }, 409));
    const error = await captureError(createUser('alice', 'k'));
    expect(error.status).toBe(409);
    expect(error.kind).toBe('conflict');
    expect(error.message).toBe("user already exists: 'alice'");
  });

  it('maps 429 to ratelimited and parses the Retry-After header', async () => {
    installFetch(
      jsonResponse(
        { error: 'rate limited - too many failed attempts, retry in 7s' },
        429,
        { 'Retry-After': '7' },
      ),
    );
    const error = await captureError(revokeConfig('alice', 'work'));
    expect(error.status).toBe(429);
    expect(error.kind).toBe('ratelimited');
    expect(error.retryAfterSeconds).toBe(7);
  });

  it('leaves retryAfterSeconds undefined when Retry-After is malformed', async () => {
    installFetch(
      jsonResponse({ error: 'rate limited' }, 429, { 'Retry-After': 'soonish' }),
    );
    const error = await captureError(listUsers());
    expect(error.kind).toBe('ratelimited');
    expect(error.retryAfterSeconds).toBeUndefined();
  });

  it('maps 503 to unavailable and keeps the server retry hint', async () => {
    installFetch(
      jsonResponse(
        { error: 'config store unavailable (database is locked) - retry shortly' },
        503,
      ),
    );
    const error = await captureError(listUsers());
    expect(error.status).toBe(503);
    expect(error.kind).toBe('unavailable');
    expect(error.message).toContain('retry shortly');
  });

  it('maps 500 to server with the generic message', async () => {
    installFetch(
      new Response('Internal Server Error', {
        status: 500,
        headers: { 'Content-Type': 'text/plain' },
      }),
    );
    const error = await captureError(deleteUser('alice'));
    expect(error.status).toBe(500);
    expect(error.kind).toBe('server');
    expect(error.message).toBe('internal server error');
  });

  it('maps network failures to the network kind without logging anything', async () => {
    const spy = vi.spyOn(console, 'log').mockImplementation(() => undefined);
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new TypeError('Failed to fetch')),
    );
    const error = await captureError(listUsers());
    expect(error.status).toBe(0);
    expect(error.kind).toBe('network');
    expect(error.message).toContain('unreachable');
    expect(spy).not.toHaveBeenCalled();
    spy.mockRestore();
  });
});

describe('auth header injection', () => {
  it('sends Authorization: Bearer <stored key> on every call', async () => {
    saveKey('secret-admin-key');
    const fetchMock = installFetch(jsonResponse({ users: [] }));
    await listUsers();
    const [, init] = fetchMock.mock.calls[0] as [unknown, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe(
      'Bearer secret-admin-key',
    );
  });

  it('omits the Authorization header when no key is stored', async () => {
    const fetchMock = installFetch(jsonResponse({ users: [] }));
    await listUsers();
    const [, init] = fetchMock.mock.calls[0] as [unknown, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });
});

describe('relative URLs only', () => {
  it('every issued fetch uses a relative /admin path (no origin, no key)', async () => {
    saveKey('secret-admin-key');
    const fetchMock = installFetch(
      (input) => {
        const url = String(input);
        if (url.endsWith('/configs') && url.startsWith('/admin/users/')) {
          return jsonResponse({ username: 'alice', configs: [] });
        }
        if (url === '/admin/users') return jsonResponse({ users: [] });
        if (url === '/admin/configs') return jsonResponse({ configs: [] });
        if (url.endsWith('/key')) return jsonResponse({ username: 'alice', rotated: true });
        if (url.endsWith('/mirror')) return jsonResponse({ revoked: true, username: 'alice', config: 'mirror' });
        return jsonResponse({ username: 'alice', configs: [] });
      },
      jsonResponse({ deleted: true, username: 'alice' }),
    );

    await listUsers();
    await getUser('alice smith');
    await listConfigs();
    await createUser('alice', 'user-key');
    await deleteUser('alice');
    await grantConfig('alice', 'mirror');
    await replaceConfigs('alice', ['mirror']);
    await revokeConfig('alice', 'mirror');
    await rotateKey('alice', 'user-key');

    const urls = fetchUrls(fetchMock);
    expect(urls).toHaveLength(9);
    for (const url of urls) {
      expect(url.startsWith('/admin/')).toBe(true);
      expect(url).not.toMatch(/^https?:/);
      expect(url).not.toContain('//admin');
    }
    // Username and config names are path-encoded, never raw.
    expect(urls).toContain('/admin/users/alice%20smith');
    expect(urls).toContain('/admin/users/alice/configs/mirror');
  });
});

describe('mutation signal extraction (success-signal surfacing)', () => {
  it('returns no signals when the body carries no signal fields', () => {
    expect(mutationSignals({ username: 'alice', configs: [] })).toEqual({
      reloadFailed: false,
      restartRequired: false,
    });
  });

  it('maps reload: "failed" to reloadFailed only', () => {
    expect(mutationSignals({ username: 'alice', reload: 'failed' })).toEqual({
      reloadFailed: true,
      restartRequired: false,
    });
  });

  it('ignores a reload value other than "failed"', () => {
    expect(mutationSignals({ reload: 'ok' }).reloadFailed).toBe(false);
  });

  it('maps config_topology_restart_required: true to restartRequired', () => {
    expect(
      mutationSignals({ username: 'alice', config_topology_restart_required: true }),
    ).toEqual({ reloadFailed: false, restartRequired: true });
  });

  it('maps restart_required: true defensively (field not emitted since M5 — synthetic body)', () => {
    // No route has emitted restart_required since the M5 hot-reload work
    // (docs/api.md "Restart and reload"); the mapping stays covered anyway.
    expect(mutationSignals({ username: 'alice', restart_required: true })).toEqual({
      reloadFailed: false,
      restartRequired: true,
    });
  });

  it('handles non-object bodies', () => {
    expect(mutationSignals(null)).toEqual({ reloadFailed: false, restartRequired: false });
    expect(mutationSignals('done')).toEqual({ reloadFailed: false, restartRequired: false });
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});
