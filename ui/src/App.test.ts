import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App.svelte';
import { loadKey, saveKey } from './lib/auth';
import { GATE_EXPIRED_MESSAGE, GATE_INVALID_MESSAGE } from './lib/gate';
import { isInsecureHttpOrigin } from './lib/origin';
import { installFetch, jsonResponse, plainTextNotFound } from './test-helpers';

// The origin helper is a pure function of location.href, which jsdom does
// not let tests reconfigure — stub it to drive the plain-HTTP banner.
vi.mock('./lib/origin', () => ({
  isInsecureHttpOrigin: vi.fn(() => false),
}));

const ALICE_LIST = jsonResponse({
  users: [{ username: 'alice', configs: ['work'] }],
});

function gateHeading(): Promise<HTMLElement> {
  return screen.findByRole('heading', { level: 1, name: 'Admin sign-in' });
}

describe('App shell', () => {
  beforeEach(() => {
    vi.mocked(isInsecureHttpOrigin).mockReturnValue(false);
  });

  it('shows the gate without touching the API when no key is stored', () => {
    const fetchMock = installFetch(jsonResponse({ users: [] }));
    render(App);
    expect(screen.getByRole('heading', { level: 1, name: 'Admin sign-in' })).toBeInTheDocument();
    expect(screen.getByLabelText('Admin key')).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('validates a stored key on mount and enters the users list', async () => {
    saveKey('stored-key');
    const fetchMock = installFetch(ALICE_LIST);
    render(App);

    // The validation payload seeds the list — exactly one GET /admin/users.
    expect(await screen.findByText('alice')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'Users' })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [unknown, RequestInit];
    expect(url).toBe('/admin/users');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer stored-key');
  });

  it('accepts a typed key at the gate and enters the users list', async () => {
    const fetchMock = installFetch(ALICE_LIST);
    render(App);

    const input = screen.getByLabelText('Admin key') as HTMLInputElement;
    input.value = 'typed-key';
    await fireEvent.input(input);
    await fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByText('alice')).toBeInTheDocument();
    expect(loadKey()).toBe('typed-key');
    // One call: the gate validation; its payload seeds the list view.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('on a stored-key 401 at mount: wipes the key and shows the invalid-key message', async () => {
    saveKey('stale-key');
    installFetch(jsonResponse({ error: 'unauthorized - missing or invalid credentials' }, 401));
    render(App);

    await gateHeading();
    expect(screen.getByRole('alert')).toHaveTextContent(GATE_INVALID_MESSAGE);
    expect(loadKey()).toBeNull();
    // No saved key to retry with.
    expect(screen.queryByRole('button', { name: 'Try saved key' })).toBeNull();
  });

  it('on a plain-text 404 at mount: shows the disabled screen, not a key problem, and keeps the key', async () => {
    saveKey('stored-key');
    installFetch(plainTextNotFound('Not Found'));
    render(App);

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'not available on this server',
    );
    expect(screen.queryByText(GATE_INVALID_MESSAGE)).toBeNull();
    expect(loadKey()).toBe('stored-key');
    expect(screen.getByRole('button', { name: 'Try saved key' })).toBeInTheDocument();
  });

  it('mid-session 401 wipes sessionStorage and returns to the gate with "key no longer valid" (no retry loop)', async () => {
    saveKey('stored-key');
    const fetchMock = installFetch(
      ALICE_LIST, // mount validation (seeds the list)
      jsonResponse({ error: 'unauthorized - missing or invalid credentials' }, 401), // refresh
    );
    render(App);
    await screen.findByText('alice');

    await fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));

    expect(await gateHeading()).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(GATE_EXPIRED_MESSAGE);
    expect(loadKey()).toBeNull();
    expect(sessionStorage.getItem('caldav-mcp-admin-key')).toBeNull();
    // Exactly two calls: mount validation and the 401 refresh — no retry
    // loop after the 401.
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('logout wipes the key and returns to the gate', async () => {
    saveKey('stored-key');
    const fetchMock = installFetch(ALICE_LIST);
    render(App);
    await screen.findByText('alice');

    await fireEvent.click(screen.getByRole('button', { name: 'Log out' }));

    expect(await gateHeading()).toBeInTheDocument();
    expect(loadKey()).toBeNull();
    // Only the mount validation — logout itself fetches nothing.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('navigates list → detail and back', async () => {
    saveKey('stored-key');
    installFetch(
      ALICE_LIST, // mount validation (seeds the list)
      jsonResponse({ username: 'alice', configs: ['work'] }), // detail: user
      jsonResponse({ configs: ['work'] }), // detail: configs
      ALICE_LIST, // back → list refetch (initialUsers was cleared)
    );
    render(App);
    await screen.findByText('alice');

    await fireEvent.click(screen.getByRole('button', { name: /alice/ }));
    expect(
      await screen.findByRole('button', { name: 'Revoke work' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1, name: 'alice' })).toBeInTheDocument();

    await fireEvent.click(screen.getByRole('button', { name: '← Users' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Users' })).toBeInTheDocument();
    expect(await screen.findByText('alice')).toBeInTheDocument();
  });

  it('shows the plain-HTTP warning only on insecure origins, dismissible', async () => {
    vi.mocked(isInsecureHttpOrigin).mockReturnValue(true);
    render(App);

    expect(
      await screen.findByText(/send the admin key over TLS or localhost only/),
    ).toBeInTheDocument();

    await fireEvent.click(screen.getByRole('button', { name: 'Dismiss warning' }));
    expect(
      screen.queryByText(/send the admin key over TLS or localhost only/),
    ).toBeNull();
  });

  it('does not show the plain-HTTP warning on secure/local origins', () => {
    render(App);
    expect(
      screen.queryByText(/send the admin key over TLS or localhost only/),
    ).toBeNull();
  });
});
