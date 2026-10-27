<script lang="ts">
  import { ApiError, deleteUser } from '../api';
  import { rateLimitSeconds, triggerRateLimit } from '../rate';

  interface Props {
    username: string;
    open?: boolean;
    onDeleted?: (body: unknown) => void;
    onNotFound?: (error: ApiError) => void;
    routeError?: (error: ApiError) => boolean;
  }

  let {
    username,
    open = $bindable(false),
    onDeleted,
    onNotFound,
    routeError = () => false,
  }: Props = $props();

  let dialogEl = $state<HTMLDialogElement | null>(null);
  let typed = $state('');
  let submitting = $state(false);
  let fieldError = $state('');

  const confirmed = $derived(typed === username && username !== '');

  $effect(() => {
    if (!dialogEl) return;
    if (open && !dialogEl.open) {
      typed = '';
      fieldError = '';
      dialogEl.showModal();
    }
    if (!open && dialogEl.open) dialogEl.close();
  });

  async function submit(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (submitting || !confirmed || $rateLimitSeconds > 0) return;
    fieldError = '';
    submitting = true;
    try {
      const body = await deleteUser(username);
      onDeleted?.(body);
      open = false;
    } catch (error) {
      if (error instanceof ApiError) {
        // Stale view (user deleted elsewhere): report and leave the detail
        // view — same recovery as a stale grant (design (d)).
        if (error.kind === 'notfound') {
          onNotFound?.(error);
          open = false;
          return;
        }
        if (routeError(error)) return;
        if (error.kind === 'ratelimited') {
          triggerRateLimit(error.retryAfterSeconds ?? 5);
        }
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
  class="dlg danger"
  bind:this={dialogEl}
  aria-labelledby="delete-user-title"
  onclose={() => (open = false)}
>
  <form onsubmit={submit}>
    <h2 id="delete-user-title">Delete user — {username}</h2>
    <p class="hint">
      This permanently removes the user and all of their config grants. There is no undo.
    </p>
    {#if fieldError}
      <p class="error" role="alert">{fieldError}</p>
    {/if}
    <div class="field">
      <label for="delete-confirm">Type <strong>{username}</strong> to confirm</label>
      <input id="delete-confirm" bind:value={typed} autocomplete="off" />
    </div>
    <div class="actions">
      <button type="button" onclick={() => (open = false)}>Cancel</button>
      <button type="submit" class="danger" disabled={!confirmed || submitting || $rateLimitSeconds > 0}>
        {submitting ? 'Deleting…' : 'Delete user'}
      </button>
    </div>
  </form>
</dialog>

<style>
  h2 {
    margin: 0 0 0.5rem;
    font-size: 1.2rem;
  }

  .hint {
    color: var(--muted);
    font-size: 0.9rem;
    margin: 0 0 0.75rem;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }

  .error {
    padding: 0.5rem 0.7rem;
    border-radius: var(--radius);
    border: 1px solid var(--danger);
    background: var(--danger-bg);
    color: var(--danger-fg);
    margin: 0 0 0.75rem;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 1rem;
  }
</style>
