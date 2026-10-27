import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import ToastList from './ToastList.svelte';
import UsersView from './UsersView.svelte';
import { installFetch, jsonResponse } from '../../test-helpers';

const USERS = [
  { username: 'alice', configs: ['work'] },
  { username: 'bob', configs: [] },
  { username: 'carol', configs: ['personal', 'work-archive'] },
];

function usersResponse(): Response {
  return jsonResponse({ users: USERS });
}

async function setfield(label: string, value: string): Promise<void> {
  const input = screen.getByLabelText(label) as HTMLInputElement;
  input.value = value;
  await fireEvent.input(input);
}

async function openCreateDialog(): Promise<void> {
  await screen.findByText('alice');
  await fireEvent.click(screen.getByRole('button', { name: 'Add user' }));
}

describe('UsersView', () => {
  it('renders users with their config chips', async () => {
    installFetch(usersResponse());
    render(UsersView, { props: { onSelect: vi.fn() } });

    expect(await screen.findByText('alice')).toBeInTheDocument();
    expect(screen.getByText('carol')).toBeInTheDocument();
    expect(screen.getByText('work')).toBeInTheDocument();
    expect(screen.getByText('work-archive')).toBeInTheDocument();
    expect(screen.getByText('no configs')).toBeInTheDocument();
  });

  it('filters client-side by username substring', async () => {
    installFetch(usersResponse());
    render(UsersView, { props: { onSelect: vi.fn() } });
    await screen.findByText('alice');

    await setfield('Filter', 'ali');
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.queryByText('bob')).toBeNull();
    expect(screen.queryByText('carol')).toBeNull();

    await setfield('Filter', 'zzz');
    expect(screen.getByText(/No users match/)).toBeInTheDocument();
    expect(screen.queryByText('alice')).toBeNull();

    await setfield('Filter', '');
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.getByText('bob')).toBeInTheDocument();
  });

  it('filters client-side by config-name substring', async () => {
    installFetch(usersResponse());
    render(UsersView, { props: { onSelect: vi.fn() } });
    await screen.findByText('alice');

    await setfield('Filter', 'archive');
    expect(screen.getByText('carol')).toBeInTheDocument();
    expect(screen.queryByText('alice')).toBeNull();
    expect(screen.queryByText('bob')).toBeNull();
  });

  it('refresh re-fetches the list', async () => {
    const fetchMock = installFetch(usersResponse(), usersResponse());
    render(UsersView, { props: { onSelect: vi.fn() } });
    await screen.findByText('alice');

    await fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it('row click selects the user', async () => {
    installFetch(usersResponse());
    const onSelect = vi.fn();
    render(UsersView, { props: { onSelect } });
    await screen.findByText('alice');

    await fireEvent.click(screen.getByRole('button', { name: /alice/ }));
    expect(onSelect).toHaveBeenCalledWith('alice');
  });

  it('creates a user — key arrives auto-generated and read-only, Regenerate swaps it, POST body carries exactly the visible value', async () => {
    const fetchMock = installFetch(
      usersResponse(),
      jsonResponse({ username: 'dave', configs: [] }, 201),
      jsonResponse({ users: [...USERS, { username: 'dave', configs: [] }] }),
    );
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });

    render(UsersView, { props: { onSelect: vi.fn() } });
    // The toast region normally lives in App — render it here too so the
    // success toast is observable in this component-level test.
    render(ToastList);
    await openCreateDialog();

    await setfield('Username', 'dave');

    // The key is generated the moment the dialog opens — never typed.
    const keyInput = screen.getByLabelText('API key') as HTMLInputElement;
    expect(keyInput).toHaveAttribute('readonly');
    expect(keyInput.value).toMatch(/^[0-9a-f]{48}$/);
    const firstKey = keyInput.value;

    // A typing attempt never changes the field.
    await fireEvent.input(keyInput, { target: { value: 'operator-typed' } });
    expect(keyInput.value).toBe(firstKey);

    // Regenerate replaces it with a fresh key and announces it live.
    await fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }));
    expect(keyInput.value).toMatch(/^[0-9a-f]{48}$/);
    expect(keyInput.value).not.toBe(firstKey);
    expect(await screen.findByText('New key generated.')).toBeInTheDocument();
    const generatedKey = keyInput.value;

    // Reveal exposes the generated value before submit.
    await fireEvent.click(screen.getByRole('button', { name: 'Reveal' }));
    expect(keyInput.type).toBe('text');
    expect(keyInput.value).toBe(generatedKey);

    // Copy hands the value over before submit.
    await fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(generatedKey));
    expect(await screen.findByText('Copied to clipboard.')).toBeInTheDocument();

    await fireEvent.click(screen.getByRole('button', { name: 'Create user' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const [, init] = fetchMock.mock.calls[1] as [unknown, RequestInit];
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({
      username: 'dave',
      key: generatedKey,
    });

    // Success toast and a refreshed list.
    expect(await screen.findByText(/User “dave” created/)).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
  });

  it('renders 400 validation errors inline in the create dialog', async () => {
    const fetchMock = installFetch(
      usersResponse(),
      jsonResponse({ error: 'username is required and must be a string' }, 400),
    );
    render(UsersView, { props: { onSelect: vi.fn() } });
    await openCreateDialog();

    // The key field is read-only and already holds a generated key.
    await setfield('Username', 'x');
    await fireEvent.click(screen.getByRole('button', { name: 'Create user' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'username is required and must be a string',
    );
    // The dialog stays open so the input can be corrected.
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('renders 409 duplicate-user errors inline in the create dialog', async () => {
    const fetchMock = installFetch(
      usersResponse(),
      jsonResponse({ error: "user already exists: 'alice'" }, 409),
    );
    render(UsersView, { props: { onSelect: vi.fn() } });
    await openCreateDialog();

    await setfield('Username', 'alice');
    await fireEvent.click(screen.getByRole('button', { name: 'Create user' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole('alert')).toHaveTextContent(
      "user already exists: 'alice'",
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('generates keys on insecure origins (getRandomValues without crypto.subtle) while Copy stays disabled without a clipboard', async () => {
    // Insecure origins expose getRandomValues but not crypto.subtle.
    vi.stubGlobal('crypto', {
      getRandomValues: (array: Uint8Array) => {
        array.fill(1);
        return array;
      },
    });
    Object.defineProperty(navigator, 'clipboard', {
      value: undefined,
      configurable: true,
    });
    installFetch(usersResponse());
    render(UsersView, { props: { onSelect: vi.fn() } });
    await openCreateDialog();

    // Generation no longer depends on crypto.subtle: the field arrives
    // populated and Regenerate is offered.
    const keyInput = screen.getByLabelText('API key') as HTMLInputElement;
    expect(keyInput).toHaveAttribute('readonly');
    expect(keyInput.value).toBe('01'.repeat(24));
    expect(screen.getByRole('button', { name: 'Regenerate' })).toBeEnabled();
    // Copy is visibly disabled rather than a silent no-op.
    expect(screen.getByRole('button', { name: 'Copy' })).toBeDisabled();
    // Reveal remains available as the clipboard fallback.
    expect(screen.getByRole('button', { name: 'Reveal' })).toBeInTheDocument();
  });

  it('keeps the key field read-only and inert when Web Crypto is missing entirely', async () => {
    vi.stubGlobal('crypto', {});
    installFetch(usersResponse());
    render(UsersView, { props: { onSelect: vi.fn() } });
    await openCreateDialog();

    // No generation possible: the field stays read-only (never typed into),
    // Regenerate is withheld, and submission is blocked instead of crashing.
    const keyInput = screen.getByLabelText('API key') as HTMLInputElement;
    expect(keyInput).toHaveAttribute('readonly');
    expect(keyInput.value).toBe('');
    expect(screen.queryByRole('button', { name: 'Regenerate' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Create user' })).toBeDisabled();
  });
});
