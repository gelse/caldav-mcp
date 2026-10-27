import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it, vi } from 'vitest';
import { ADMIN_KEY_STORAGE, loadKey, saveKey } from '../auth';
import { GATE_INVALID_MESSAGE } from '../gate';
import { triggerRateLimit } from '../rate';
import GateView from './GateView.svelte';
import { installFetch, jsonResponse, plainTextNotFound } from '../../test-helpers';

async function typeKey(value: string): Promise<void> {
  const input = screen.getByLabelText('Admin key') as HTMLInputElement;
  input.value = value;
  await fireEvent.input(input);
}

describe('GateView', () => {
  it('accepts a valid key, stores it, and enters the app', async () => {
    const fetchMock = installFetch(jsonResponse({ users: [] }));
    const onUnlock = vi.fn();
    render(GateView, { props: { onUnlock } });

    await typeKey('correct-horse');
    await fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    await waitFor(() => expect(onUnlock).toHaveBeenCalledTimes(1));
    expect(loadKey()).toBe('correct-horse');
    expect(sessionStorage.getItem(ADMIN_KEY_STORAGE)).toBe('correct-horse');
    const [url, init] = fetchMock.mock.calls[0] as [unknown, RequestInit];
    expect(url).toBe('/admin/users');
    expect((init.headers as Record<string, string>).Authorization).toBe(
      'Bearer correct-horse',
    );
  });

  it('on 401 wipes storage and shows the invalid-key message', async () => {
    saveKey('stale-key');
    installFetch(
      jsonResponse({ error: 'unauthorized - missing or invalid credentials' }, 401),
    );
    const onUnlock = vi.fn();
    render(GateView, { props: { onUnlock } });

    await typeKey('wrong-key');
    await fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(GATE_INVALID_MESSAGE);
    expect(onUnlock).not.toHaveBeenCalled();
    expect(loadKey()).toBeNull();
    expect(sessionStorage.getItem(ADMIN_KEY_STORAGE)).toBeNull();
    // The wrong value is cleared from the field too.
    expect((screen.getByLabelText('Admin key') as HTMLInputElement).value).toBe('');
  });

  it('shows the dedicated disabled screen on a plain-text 404, distinct from wrong-key', async () => {
    installFetch(plainTextNotFound('Not Found'));
    const onUnlock = vi.fn();
    render(GateView, { props: { onUnlock } });

    await typeKey('any-key');
    await fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'not available on this server',
    );
    expect(screen.getByRole('alert')).toHaveTextContent('not a key problem');
    // Distinct from the wrong-key screen.
    expect(screen.queryByText(GATE_INVALID_MESSAGE)).toBeNull();
    expect(onUnlock).not.toHaveBeenCalled();
  });

  it('renders the disabled screen from an initial status (silent validation path)', () => {
    render(GateView, {
      props: {
        initialStatus: 'disabled',
        initialMessage: 'The admin API is not available on this server',
        hasStoredKey: true,
        onUnlock: vi.fn(),
      },
    });
    expect(screen.getByRole('alert')).toHaveTextContent('not available on this server');
    expect(screen.getByRole('button', { name: 'Try saved key' })).toBeInTheDocument();
  });

  it('on 429 shows a countdown, disables the form, then re-enables it', async () => {
    installFetch(
      jsonResponse(
        { error: 'rate limited - too many failed attempts, retry in 3s' },
        429,
        { 'Retry-After': '3' },
      ),
    );
    render(GateView, { props: { onUnlock: vi.fn() } });

    await typeKey('some-key');
    await fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByRole('status')).toHaveTextContent('retry in 3s');
    expect(screen.getByLabelText('Admin key')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeDisabled();
  });

  it('ticks the initial rate-limited countdown down and lifts the limit', async () => {
    vi.useFakeTimers();
    try {
      render(GateView, {
        props: {
          initialStatus: 'ratelimited',
          initialMessage: 'Too many failed attempts',
          initialRetryAfter: 2,
          onUnlock: vi.fn(),
        },
      });

      expect(screen.getByRole('status')).toHaveTextContent('retry in 2s');
      expect(screen.getByLabelText('Admin key')).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Sign in' })).toBeDisabled();

      await vi.advanceTimersByTimeAsync(1000);
      expect(screen.getByRole('status')).toHaveTextContent('retry in 1s');
      expect(screen.getByLabelText('Admin key')).toBeDisabled();

      await vi.advanceTimersByTimeAsync(1000);
      expect(screen.getByRole('status')).toHaveTextContent('Rate limit lifted');
      // Forms re-enable after the countdown.
      expect(screen.getByLabelText('Admin key')).toBeEnabled();
      await typeKey('some-key');
      expect(screen.getByRole('button', { name: 'Sign in' })).toBeEnabled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows the unreachable screen when the network fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new TypeError('Failed to fetch')),
    );
    const onUnlock = vi.fn();
    render(GateView, { props: { onUnlock } });

    await typeKey('some-key');
    await fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('unreachable');
    expect(onUnlock).not.toHaveBeenCalled();
  });

  it('validates a stored key via the Try-saved-key button', async () => {
    saveKey('stored-key');
    const fetchMock = installFetch(jsonResponse({ users: [] }));
    const onUnlock = vi.fn();
    render(GateView, {
      props: {
        initialStatus: 'unreachable',
        initialMessage: 'Server unreachable',
        hasStoredKey: true,
        onUnlock,
      },
    });

    await fireEvent.click(screen.getByRole('button', { name: 'Try saved key' }));
    await waitFor(() => expect(onUnlock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('explains a shared rate-limit countdown even on the idle gate (e.g. after logout)', () => {
    triggerRateLimit(4);
    render(GateView, { props: { onUnlock: vi.fn() } });

    expect(screen.getByRole('status')).toHaveTextContent('retry in 4s');
    expect(screen.getByLabelText('Admin key')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeDisabled();
  });
});
