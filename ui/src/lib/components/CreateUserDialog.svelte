<script lang="ts">
  import { ApiError, createUser, type User } from '../api';
  import { canGenerateKey, generateUserKey } from '../keygen';
  import { rateLimitSeconds, triggerRateLimit } from '../rate';
  import { reportMutation } from '../toasts';
  import KeyField from './KeyField.svelte';

  interface Props {
    open?: boolean;
    onCreated?: (user: User) => void;
    routeError?: (error: ApiError) => boolean;
  }

  let {
    open = $bindable(false),
    onCreated,
    routeError = () => false,
  }: Props = $props();

  let dialogEl = $state<HTMLDialogElement | null>(null);
  let username = $state('');
  let key = $state('');
  let submitting = $state(false);
  let fieldError = $state('');

  $effect(() => {
    if (!dialogEl) return;
    if (open && !dialogEl.open) {
      // Fresh attempt every time the dialog opens — no stale error or
      // leftover key material from a cancelled attempt, and a freshly
      // generated key so the operator never types one.
      fieldError = '';
      username = '';
      key = canGenerateKey() ? generateUserKey() : '';
      dialogEl.showModal();
    }
    if (!open && dialogEl.open) dialogEl.close();
  });

  function close(): void {
    open = false;
  }

  async function submit(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (submitting || $rateLimitSeconds > 0) return;
    const enteredUsername = username.trim();
    const enteredKey = key; // exact generated value shown in the field
    fieldError = '';
    submitting = true;
    try {
      const created = await createUser(enteredUsername, enteredKey);
      reportMutation(created, `User “${created.username}” created — applied live.`);
      onCreated?.(created);
      open = false;
      username = '';
      key = '';
    } catch (error) {
      if (error instanceof ApiError) {
        if (routeError(error)) return;
        if (error.kind === 'ratelimited') {
          triggerRateLimit(error.retryAfterSeconds ?? 5);
        }
        // 400/409 (and 503's retry hint) render inline, next to the form.
        fieldError = error.message;
      } else {
        fieldError = 'Something went wrong — try again.';
      }
    } finally {
      submitting = false;
    }
  }
</script>

<dialog
  class="dlg"
  bind:this={dialogEl}
  aria-labelledby="create-user-title"
  onclose={() => (open = false)}
>
  <form onsubmit={submit}>
    <h2 id="create-user-title">Add user</h2>
    {#if fieldError}
      <p class="error" role="alert">{fieldError}</p>
    {/if}
    <div class="field">
      <label for="create-username">Username</label>
      <input id="create-username" bind:value={username} autocomplete="off" required />
    </div>
    {#key open}
      <!-- Remounted per open so reveal/copy state starts fresh. -->
      <KeyField id="create-key" label="API key" bind:value={key} />
    {/key}
    <p class="hint">
      A new API key has been generated — the server stores only its hash. Reveal and copy it before
      you close this dialog; it cannot be shown again.
    </p>
    <div class="actions">
      <button type="button" onclick={close}>Cancel</button>
      <button
        type="submit"
        class="primary"
        disabled={submitting || !username.trim() || !key || $rateLimitSeconds > 0}
      >
        {submitting ? 'Creating…' : 'Create user'}
      </button>
    </div>
  </form>
</dialog>

<style>
  h2 {
    margin: 0 0 0.75rem;
    font-size: 1.2rem;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    margin-bottom: 0.75rem;
  }

  .error {
    padding: 0.5rem 0.7rem;
    border-radius: var(--radius);
    border: 1px solid var(--danger);
    background: var(--danger-bg);
    color: var(--danger-fg);
    margin: 0 0 0.75rem;
  }

  .hint {
    font-size: 0.85rem;
    color: var(--muted);
    margin: 0.5rem 0 0;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 1rem;
  }
</style>
