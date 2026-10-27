<script lang="ts">
  import { listUsers, type User } from '../api';
  import { clearKey, saveKey } from '../auth';
  import { gateOutcomeFor, type GateStatus } from '../gate';
  import { rateLimitSeconds, triggerRateLimit } from '../rate';

  interface Props {
    initialStatus?: GateStatus;
    initialMessage?: string;
    initialRetryAfter?: number;
    hasStoredKey?: boolean;
    onUnlock: (users: User[]) => void;
  }

  let {
    initialStatus = 'idle',
    initialMessage = '',
    initialRetryAfter = 0,
    hasStoredKey = false,
    onUnlock,
  }: Props = $props();

  // Local copies — App passes these once, when it mounts the gate (the
  // gate is remounted on every entry, so one-shot capture is intended).
  // svelte-ignore state_referenced_locally
  let status = $state<GateStatus>(initialStatus);
  // svelte-ignore state_referenced_locally
  let statusMessage = $state(initialMessage);
  // svelte-ignore state_referenced_locally
  let retryAfter = $state(initialRetryAfter);
  // svelte-ignore state_referenced_locally
  let countdown = $state(initialStatus === 'ratelimited' ? initialRetryAfter : 0);

  let keyInput = $state('');
  let busy = $state(false);

  // Shared countdown: any earlier 429 anywhere keeps forms disabled.
  const sharedRateLimited = $derived($rateLimitSeconds > 0);
  const rateLimited = $derived(status === 'ratelimited' && countdown > 0);
  const formDisabled = $derived(busy || rateLimited || sharedRateLimited);

  // Local countdown for this gate's own rate-limited screen.
  $effect(() => {
    if (status !== 'ratelimited' || retryAfter <= 0) return;
    let remaining = retryAfter;
    countdown = remaining;
    const interval = setInterval(() => {
      remaining -= 1;
      countdown = remaining;
      if (remaining <= 0) clearInterval(interval);
    }, 1000);
    return () => clearInterval(interval);
  });

  function applyOutcome(error: unknown): void {
    const outcome = gateOutcomeFor(error, false);
    status = outcome.status;
    statusMessage = outcome.message;
    retryAfter = outcome.retryAfter;
    if (outcome.status === 'ratelimited') {
      triggerRateLimit(outcome.retryAfter);
    }
    if (outcome.status === 'invalid' || outcome.status === 'expired') {
      // Wrong/stale key — wipe it so nothing invalid lingers (design (c)).
      clearKey();
      keyInput = '';
    }
  }

  async function validateKey(): Promise<void> {
    busy = true;
    try {
      // The validated payload seeds the users view — no second fetch.
      const users = await listUsers();
      onUnlock(users);
    } catch (error) {
      applyOutcome(error);
    } finally {
      busy = false;
    }
  }

  function submit(event: SubmitEvent): void {
    event.preventDefault();
    if (formDisabled || !keyInput) return;
    // Persist first — validation reads the key back through the auth store.
    saveKey(keyInput);
    void validateKey();
  }
</script>

<h1 tabindex="-1">Admin sign-in</h1>

<!-- Shared countdown: a 429 received anywhere (e.g. the IP is already
     rate-limited when the gate mounts or after logout) explains itself
     here even when this gate's own status is not `ratelimited`. -->
{#if $rateLimitSeconds > 0 && status !== 'ratelimited'}
  <p class="screen-note" role="status">Rate limited — retry in {$rateLimitSeconds}s.</p>
{/if}

{#if status === 'ratelimited'}
  <p class="screen-note" role="status">
    {countdown > 0 ? `Rate limited — retry in ${countdown}s.` : 'Rate limit lifted — try again.'}
  </p>
{:else if status !== 'idle'}
  <p class="screen-note" role="alert">{statusMessage}</p>
{/if}

<form class="gate-form" onsubmit={submit}>
  <label for="admin-key">Admin key</label>
  <input
    id="admin-key"
    type="password"
    bind:value={keyInput}
    autocomplete="off"
    disabled={formDisabled}
    required
  />
  <div class="actions">
    <button type="submit" class="primary" disabled={formDisabled || !keyInput}>
      {busy ? 'Checking…' : 'Sign in'}
    </button>
    {#if hasStoredKey && (status === 'unreachable' || status === 'disabled' || (status === 'ratelimited' && countdown <= 0))}
      <button type="button" onclick={() => void validateKey()} disabled={formDisabled}>
        Try saved key
      </button>
    {/if}
  </div>
</form>

<style>
  h1 {
    margin: 0 0 0.75rem;
    font-size: 1.5rem;
  }

  h1:focus {
    outline: none;
  }

  h1:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .screen-note {
    padding: 0.6rem 0.8rem;
    border-radius: var(--radius);
    border: 1px solid var(--border);
    background: var(--surface);
    margin: 0 0 1rem;
  }

  .gate-form {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    max-width: 26rem;
  }

  .actions {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
  }
</style>
