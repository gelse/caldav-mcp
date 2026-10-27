<script lang="ts">
  import { ApiError, rotateKey } from '../api';
  import { rateLimitSeconds, triggerRateLimit } from '../rate';
  import KeyField from './KeyField.svelte';

  interface Props {
    username: string;
    open?: boolean;
    onRotated?: (body: unknown) => void;
    onNotFound?: (error: ApiError) => void;
    routeError?: (error: ApiError) => boolean;
  }

  let {
    username,
    open = $bindable(false),
    onRotated,
    onNotFound,
    routeError = () => false,
  }: Props = $props();

  let dialogEl = $state<HTMLDialogElement | null>(null);
  let newKey = $state('');
  let confirmed = $state(false);
  let submitting = $state(false);
  let fieldError = $state('');
  let done = $state(false);

  $effect(() => {
    if (!dialogEl) return;
    if (open && !dialogEl.open) {
      // Fresh attempt every time the dialog opens — no leftovers from a
      // cancelled or successful previous attempt.
      done = false;
      fieldError = '';
      newKey = '';
      confirmed = false;
      dialogEl.showModal();
    }
    if (!open && dialogEl.open) dialogEl.close();
  });

  function close(): void {
    open = false;
  }

  async function submit(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (submitting || $rateLimitSeconds > 0 || !confirmed || !newKey) return;
    fieldError = '';
    submitting = true;
    try {
      const body = await rotateKey(username, newKey);
      // Never echo key material: drop the value before rendering the
      // success state — the response carries none either (design (d)).
      newKey = '';
      confirmed = false;
      done = true;
      onRotated?.(body);
    } catch (error) {
      if (error instanceof ApiError) {
        // Stale detail view (user deleted elsewhere): same recovery as a
        // stale grant — report and leave the detail view (design (d)).
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
  class="dlg"
  bind:this={dialogEl}
  aria-labelledby="rotate-key-title"
  onclose={() => (open = false)}
>
  {#if done}
    <div class="success-panel">
      <h2 id="rotate-key-title">Key rotated</h2>
      <p role="status">
        Key rotated — use the key you just set for future MCP calls. The previous key stopped
        working immediately.
      </p>
      <p class="hint">This dialog cannot show the key again — the server keeps only its hash.</p>
      <div class="actions">
        <button type="button" class="primary" onclick={close}>Done</button>
      </div>
    </div>
  {:else}
    <form onsubmit={submit}>
      <h2 id="rotate-key-title">Rotate key — {username}</h2>
      <p class="hint">
        Set a new API key for this user. The current key stops working on the next MCP request.
        Reveal and copy the new key before submitting — after that only its hash exists.
      </p>
      {#if fieldError}
        <p class="error" role="alert">{fieldError}</p>
      {/if}
      {#key open}
        <!-- Remounted per open so reveal/copy state starts fresh. -->
        <KeyField id="rotate-key" label="New API key" bind:value={newKey} />
      {/key}
      <label class="check">
        <input type="checkbox" bind:checked={confirmed} />
        I understand the current key stops working immediately.
      </label>
      <div class="actions">
        <button type="button" onclick={close}>Cancel</button>
        <button
          type="submit"
          class="primary"
          disabled={submitting || !confirmed || !newKey || $rateLimitSeconds > 0}
        >
          {submitting ? 'Rotating…' : 'Rotate key'}
        </button>
      </div>
    </form>
  {/if}
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

  .error {
    padding: 0.5rem 0.7rem;
    border-radius: var(--radius);
    border: 1px solid var(--danger);
    background: var(--danger-bg);
    color: var(--danger-fg);
    margin: 0 0 0.75rem;
  }

  .check {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-top: 0.75rem;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 1rem;
  }

  .success-panel p[role='status'] {
    padding: 0.6rem 0.8rem;
    border-radius: var(--radius);
    border: 1px solid var(--border);
    background: var(--surface);
  }
</style>
