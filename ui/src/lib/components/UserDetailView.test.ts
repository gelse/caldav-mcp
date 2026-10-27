import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import type { ApiError } from '../api';
import BannerList from './BannerList.svelte';
import ToastList from './ToastList.svelte';
import UserDetailView from './UserDetailView.svelte';
import { installFetch, jsonResponse, plainTextNotFound } from '../../test-helpers';

const ALICE = { username: 'alice', configs: ['work', 'home'] };
const CONFIG_LIST = ['work', 'home', 'mirror', 'broken'];

function initialQueue(extra: Array<Response>): Array<Response> {
  return [jsonResponse(ALICE), jsonResponse({ configs: CONFIG_LIST }), ...extra];
}

async function mountDetail(
  extra: Array<Response>,
  props: Record<string, unknown> = {},
): Promise<{ onBack: ReturnType<typeof vi.fn> }> {
  const onBack = vi.fn();
  installFetch(...initialQueue(extra));
  render(UserDetailView, { props: { username: 'alice', onBack, ...props } });
  // The section only renders once the user payload arrived.
  await screen.findByRole('heading', { name: 'Config grants' });
  return { onBack };
}

async function openReplaceDialog(): Promise<void> {
  await fireEvent.click(screen.getByRole('button', { name: 'Replace set…' }));
  await screen.findByRole('dialog', { name: /Replace config grants/ });
}

async function openRotateDialog(): Promise<void> {
  await fireEvent.click(screen.getByRole('button', { name: 'Rotate key…' }));
  await screen.findByRole('dialog', { name: /Rotate key/ });
}

async function openDeleteDialog(): Promise<void> {
  await fireEvent.click(screen.getByRole('button', { name: 'Delete user…' }));
  await screen.findByRole('dialog', { name: /Delete user/ });
}

async function setField(label: string | RegExp, value: string): Promise<void> {
  const input = screen.getByLabelText(label) as HTMLInputElement;
  input.value = value;
  await fireEvent.input(input);
}

describe('UserDetailView — read', () => {
  it('renders the user with config chips and a grant dropdown fed by /admin/configs', async () => {
    await mountDetail([]);
    expect(screen.getByRole('heading', { level: 1, name: 'alice' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Revoke work' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Revoke home' })).toBeInTheDocument();

    const select = screen.getByRole('combobox', { name: 'Grant config' });
    const options = Array.from(select.querySelectorAll('option')).map(
      (option) => option.textContent ?? '',
    );
    expect(options).toEqual(['Choose a config…', 'work', 'home', 'mirror', 'broken']);
    expect(screen.getByRole('button', { name: 'Refresh configs' })).toBeInTheDocument();
  });

  it('renders an explicit note when the user has no configs', async () => {
    const onBack = vi.fn();
    installFetch(
      jsonResponse({ username: 'alice', configs: [] }),
      jsonResponse({ configs: CONFIG_LIST }),
    );
    render(UserDetailView, { props: { username: 'alice', onBack } });
    expect(await screen.findByText('No configs granted.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Revoke/ })).toBeNull();
  });

  it('falls back to the list with a toast when the detail view is stale (404 on load)', async () => {
    const onBack = vi.fn();
    installFetch(
      jsonResponse({ error: "user not found: 'alice'" }, 404),
      jsonResponse({ configs: CONFIG_LIST }),
    );
    render(UserDetailView, { props: { username: 'alice', onBack } });
    await waitFor(() => expect(onBack).toHaveBeenCalledTimes(1));
  });

  it('refresh re-fetches the config list', async () => {
    await mountDetail([jsonResponse({ configs: ['fresh'] })]);
    await fireEvent.click(screen.getByRole('button', { name: 'Refresh configs' }));
    await waitFor(() => {
      const select = screen.getByRole('combobox', { name: 'Grant config' });
      expect(select).toHaveTextContent('fresh');
    });
  });

  it('degrades the grant dropdown to a note when the admin API is disabled (plain-text 404)', async () => {
    installFetch(jsonResponse(ALICE), plainTextNotFound('Not Found'));
    render(UserDetailView, { props: { username: 'alice', onBack: vi.fn() } });
    expect(
      await screen.findByText(/Config discovery is unavailable/),
    ).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).toBeNull();
  });

  it('shows the CLI note when the store has no configs', async () => {
    installFetch(jsonResponse(ALICE), jsonResponse({ configs: [] }));
    render(UserDetailView, { props: { username: 'alice', onBack: vi.fn() } });
    expect(await screen.findByText(/No configs exist — create them via the CLI/)).toBeInTheDocument();
    expect(screen.queryByRole('combobox')).toBeNull();
  });
});

describe('UserDetailView — grant/revoke/replace', () => {
  it('grants a config: POST body {config}, chip appears, applied-live toast', async () => {
    await mountDetail([
      jsonResponse({ username: 'alice', configs: ['work', 'home', 'mirror'] }),
    ]);
    render(ToastList);

    await fireEvent.change(screen.getByRole('combobox', { name: 'Grant config' }), {
      target: { value: 'mirror' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Grant' }));

    expect(await screen.findByRole('button', { name: 'Revoke mirror' })).toBeInTheDocument();
    expect(await screen.findByText(/Granted “mirror” — applied live/)).toBeInTheDocument();
  });

  it('sends the grant request with the right method, path, and body', async () => {
    const fetchMock = installFetch(...initialQueue([
      jsonResponse({ username: 'alice', configs: ['work', 'home', 'mirror'] }),
    ]));
    render(UserDetailView, { props: { username: 'alice', onBack: vi.fn() } });
    await screen.findByRole('heading', { name: 'Config grants' });

    await fireEvent.change(screen.getByRole('combobox', { name: 'Grant config' }), {
      target: { value: 'mirror' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Grant' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));

    const [url, init] = fetchMock.mock.calls[2] as [unknown, RequestInit];
    expect(url).toBe('/admin/users/alice/configs');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ config: 'mirror' });
  });

  it('revokes via the chip: DELETE, idempotent 200 surfaces "revoked"', async () => {
    await mountDetail([
      jsonResponse({ revoked: true, username: 'alice', config: 'work' }),
    ]);
    render(ToastList);

    await fireEvent.click(screen.getByRole('button', { name: 'Revoke work' }));

    expect(await screen.findByText(/Revoked “work” — applied live/)).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Revoke work' })).toBeNull(),
    );
    // home stays
    expect(screen.getByRole('button', { name: 'Revoke home' })).toBeInTheDocument();
  });

  it('sends the revoke DELETE to the right path', async () => {
    const fetchMock = installFetch(...initialQueue([
      jsonResponse({ revoked: true, username: 'alice', config: 'work' }),
    ]));
    render(UserDetailView, { props: { username: 'alice', onBack: vi.fn() } });
    await screen.findByRole('heading', { name: 'Config grants' });

    await fireEvent.click(screen.getByRole('button', { name: 'Revoke work' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const [url, init] = fetchMock.mock.calls[2] as [unknown, RequestInit];
    expect(url).toBe('/admin/users/alice/configs/work');
    expect(init.method).toBe('DELETE');
  });

  it('replaces the grant set: pre-checked dialog → PUT {configs} with the chosen set', async () => {
    const fetchMock = installFetch(...initialQueue([
      jsonResponse({ username: 'alice', configs: ['work', 'home', 'mirror'] }),
    ]));
    render(UserDetailView, { props: { username: 'alice', onBack: vi.fn() } });
    render(ToastList);
    await screen.findByRole('heading', { name: 'Config grants' });

    await openReplaceDialog();
    const workBox = screen.getByRole('checkbox', { name: 'work' }) as HTMLInputElement;
    const homeBox = screen.getByRole('checkbox', { name: 'home' }) as HTMLInputElement;
    const mirrorBox = screen.getByRole('checkbox', { name: 'mirror' }) as HTMLInputElement;
    expect(workBox.checked).toBe(true);
    expect(homeBox.checked).toBe(true);
    expect(mirrorBox.checked).toBe(false);

    await fireEvent.click(mirrorBox);
    await fireEvent.click(screen.getByRole('button', { name: 'Replace grants' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const [url, init] = fetchMock.mock.calls[2] as [unknown, RequestInit];
    expect(url).toBe('/admin/users/alice/configs');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(String(init.body))).toEqual({
      configs: ['work', 'home', 'mirror'],
    });

    expect(await screen.findByText(/Config grants replaced/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Revoke mirror' })).toBeInTheDocument();
  });

  it('replace with everything unchecked sends {configs: []} and clears chips without a topology banner', async () => {
    await mountDetail([jsonResponse({ username: 'alice', configs: [] })]);
    render(ToastList);
    render(BannerList);

    await openReplaceDialog();
    await fireEvent.click(screen.getByRole('checkbox', { name: 'work' }));
    await fireEvent.click(screen.getByRole('checkbox', { name: 'home' }));
    await fireEvent.click(screen.getByRole('button', { name: 'Replace grants' }));

    expect(await screen.findByText('No configs granted.')).toBeInTheDocument();
    expect(await screen.findByText(/Config grants replaced/)).toBeInTheDocument();
    expect(screen.queryByText(/topology/)).toBeNull();
  });

  it('grant on a stale user (404 JSON) toasts and returns to the list', async () => {
    const { onBack } = await mountDetail([
      jsonResponse({ error: "user not found: 'alice'" }, 404),
    ]);
    render(ToastList);

    await fireEvent.change(screen.getByRole('combobox', { name: 'Grant config' }), {
      target: { value: 'mirror' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Grant' }));

    await waitFor(() => expect(onBack).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('alert')).toHaveTextContent("user not found: 'alice'");
  });
});

describe('UserDetailView — rotate and delete', () => {
  it('rotate key: confirm required, key is auto-generated read-only, PUT body carries exactly the visible value, success shows no key material', async () => {
    const fetchMock = installFetch(...initialQueue([
      jsonResponse({ username: 'alice', rotated: true }),
    ]));
    render(UserDetailView, { props: { username: 'alice', onBack: vi.fn() } });
    await screen.findByRole('heading', { name: 'Config grants' });

    await openRotateDialog();
    const submit = screen.getByRole('button', { name: 'Rotate key' });
    const keyInput = screen.getByLabelText('New API key') as HTMLInputElement;

    // The key is generated the moment the dialog opens — never typed.
    expect(keyInput).toHaveAttribute('readonly');
    expect(keyInput.value).toMatch(/^[0-9a-f]{48}$/);
    // Confirmation alone gates submit even though a key is present.
    expect(submit).toBeDisabled();

    // A typing attempt never changes the field.
    const firstKey = keyInput.value;
    await fireEvent.input(keyInput, { target: { value: 'brand-new-secret-42' } });
    expect(keyInput.value).toBe(firstKey);

    // Regenerate replaces it with a fresh key and announces it live.
    await fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }));
    expect(keyInput.value).toMatch(/^[0-9a-f]{48}$/);
    expect(keyInput.value).not.toBe(firstKey);
    expect(await screen.findByText('New key generated.')).toBeInTheDocument();
    const generatedKey = keyInput.value;

    await fireEvent.click(
      screen.getByRole('checkbox', { name: /I understand the current key stops working/ }),
    );
    expect(submit).toBeEnabled();
    await fireEvent.click(submit);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const [url, init] = fetchMock.mock.calls[2] as [unknown, RequestInit];
    expect(url).toBe('/admin/users/alice/key');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(String(init.body))).toEqual({ key: generatedKey });

    // Success state: references the generated key, shows no key material.
    expect(
      await screen.findByText(/use the newly generated key for future MCP calls/),
    ).toBeInTheDocument();
    const dialog = screen.getByRole('dialog', { name: /Key rotated/ });
    expect(dialog.textContent).not.toContain(generatedKey);
    expect(screen.queryByLabelText('New API key')).toBeNull();
  });

  it('delete: typed username confirmation gates the destructive action', async () => {
    const onBack = vi.fn();
    const fetchMock = installFetch(...initialQueue([
      jsonResponse({ deleted: true, username: 'alice' }),
    ]));
    render(UserDetailView, { props: { username: 'alice', onBack } });
    render(ToastList);
    await screen.findByRole('heading', { name: 'Config grants' });

    await openDeleteDialog();
    const submit = screen.getByRole('button', { name: 'Delete user' });
    expect(submit).toBeDisabled();

    await setField(/Type alice to confirm/, 'ali');
    expect(submit).toBeDisabled();

    await setField(/Type alice to confirm/, 'alice');
    expect(submit).toBeEnabled();
    await fireEvent.click(submit);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    const [url, init] = fetchMock.mock.calls[2] as [unknown, RequestInit];
    expect(url).toBe('/admin/users/alice');
    expect(init.method).toBe('DELETE');
    await waitFor(() => expect(onBack).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/User “alice” deleted/)).toBeInTheDocument();
  });

  it('delete of a stale user (404 JSON) toasts and returns to the list', async () => {
    const onBack = vi.fn();
    installFetch(...initialQueue([
      jsonResponse({ error: "user not found: 'alice'" }, 404),
    ]));
    render(UserDetailView, { props: { username: 'alice', onBack } });
    render(ToastList);
    await screen.findByRole('heading', { name: 'Config grants' });

    await openDeleteDialog();
    await setField(/Type alice to confirm/, 'alice');
    await fireEvent.click(screen.getByRole('button', { name: 'Delete user' }));

    await waitFor(() => expect(onBack).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('alert')).toHaveTextContent("user not found: 'alice'");
  });

  it('rotate on a stale user (404 JSON) toasts and returns to the list', async () => {
    const onBack = vi.fn();
    installFetch(...initialQueue([
      jsonResponse({ error: "user not found: 'alice'" }, 404),
    ]));
    render(UserDetailView, { props: { username: 'alice', onBack } });
    render(ToastList);
    await screen.findByRole('heading', { name: 'Config grants' });

    await openRotateDialog();
    // The read-only field arrives with a generated key — no typing needed.
    await fireEvent.click(
      screen.getByRole('checkbox', { name: /I understand the current key stops working/ }),
    );
    await fireEvent.click(screen.getByRole('button', { name: 'Rotate key' }));

    await waitFor(() => expect(onBack).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('alert')).toHaveTextContent("user not found: 'alice'");
  });

  it('replace on a stale user (404 JSON) toasts and returns to the list', async () => {
    const onBack = vi.fn();
    installFetch(...initialQueue([
      jsonResponse({ error: "user not found: 'alice'" }, 404),
    ]));
    render(UserDetailView, { props: { username: 'alice', onBack } });
    render(ToastList);
    await screen.findByRole('heading', { name: 'Config grants' });

    await openReplaceDialog();
    await fireEvent.click(screen.getByRole('button', { name: 'Replace grants' }));

    await waitFor(() => expect(onBack).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole('alert')).toHaveTextContent("user not found: 'alice'");
  });
});

describe('UserDetailView — banner mapping for success signals', () => {
  async function grantWith(body: Record<string, unknown>): Promise<void> {
    await mountDetail([jsonResponse(body)]);
    render(BannerList);
    await fireEvent.change(screen.getByRole('combobox', { name: 'Grant config' }), {
      target: { value: 'mirror' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Grant' }));
    await screen.findByText(/server restart applies it|started topology/);
  }

  it('reload: "failed" → warning banner about the failed live-apply', async () => {
    await grantWith({
      username: 'alice',
      configs: ['work', 'home', 'mirror'],
      reload: 'failed',
    });
    expect(
      screen.getByText('Saved, but live-apply failed — a server restart applies it.'),
    ).toBeInTheDocument();
  });

  it('config_topology_restart_required: true → topology restart banner', async () => {
    await grantWith({
      username: 'alice',
      configs: ['work', 'home', 'mirror'],
      config_topology_restart_required: true,
    });
    expect(screen.getByText(/not in the server’s started topology/)).toBeInTheDocument();
  });

  it('restart_required: true maps to the same banner (synthetic — not emitted by the current API)', async () => {
    // No route has emitted restart_required since M5; the mapping is
    // defensive and covered here with a synthetic body.
    await grantWith({
      username: 'alice',
      configs: ['work', 'home', 'mirror'],
      restart_required: true,
    });
    expect(screen.getByText(/not in the server’s started topology/)).toBeInTheDocument();
  });
});

describe('UserDetailView — rate limiting and session errors', () => {
  it('429 shows the Retry-After countdown and disables the forms', async () => {
    await mountDetail([
      jsonResponse({ error: 'rate limited - too many failed attempts, retry in 4s' }, 429, {
        'Retry-After': '4',
      }),
    ]);

    await fireEvent.click(screen.getByRole('button', { name: 'Revoke work' }));

    expect(await screen.findByText('Rate limited — retry in 4s.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Revoke home' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Grant' })).toBeDisabled();
    expect(screen.getByRole('combobox', { name: 'Grant config' })).toBeDisabled();
  });

  it('a mid-session 401 is routed to the shell (no local retry loop, no toast)', async () => {
    const routeError = vi.fn((_error: ApiError) => true);
    await mountDetail(
      [jsonResponse({ error: 'unauthorized - missing or invalid credentials' }, 401)],
      { routeError },
    );
    render(ToastList);

    await fireEvent.click(screen.getByRole('button', { name: 'Revoke work' }));

    await waitFor(() => expect(routeError).toHaveBeenCalledTimes(1));
    const routed = routeError.mock.calls[0][0];
    expect(routed.kind).toBe('auth');
    expect(screen.queryByText(/unauthorized/)).toBeNull();
  });
});
