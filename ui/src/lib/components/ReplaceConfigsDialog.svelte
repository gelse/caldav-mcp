<script lang="ts">
  import { ApiError, replaceConfigs, type User } from '../api';
  import { rateLimitSeconds, triggerRateLimit } from '../rate';

  interface Props {
    username: string;
    configs: string[];
    current: string[];
    open?: boolean;
    onReplaced?: (user: User) => void;
    onNotFound?: (error: ApiError) => void;
    routeError?: (error: ApiError) => boolean;
  }

  let {
    username,
    configs,
    current,
    open = $bindable(false),
    onReplaced,
    onNotFound,
    routeError = () => false,
  }: Props = $props();

  let dialogEl = $state<HTMLDialogElement | null>(null);
  let chosen = $state<string[]>([]);
  let submitting = $state(false);
  let fieldError = $state('');

  $effect(() => {
    if (!dialogEl) return;
    if (open && !dialogEl.open) {
      // Re-seed the checkboxes from the user's current grants on every open.
      chosen = configs.filter((config) => current.includes(config));
      fieldError = '';
      dialogEl.showModal();
    }
    if (!open && dialogEl.open) dialogEl.close();
  });

  async function submit(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (submitting || $rateLimitSeconds > 0) return;
    fieldError = '';
    submitting = true;
    try {
      // Set semantics: dedupe is server-side; order follows the dialog list.
      const replaced = await replaceConfigs(username, [...new Set(chosen)]);
      onReplaced?.(replaced);
      open = false;
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
        // 400 (single-passthrough), 409 (FK race), 503 (store down) —
        // render inline next to the form.
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
  aria-labelledby="replace-configs-title"
  onclose={() => (open = false)}
>
  <form onsubmit={submit}>
    <h2 id="replace-configs-title">Replace config grants — {username}</h2>
    <p class="hint">Every checked config will be granted; unchecked ones are revoked.</p>
    {#if fieldError}
      <p class="error" role="alert">{fieldError}</p>
    {/if}
    {#if configs.length === 0}
      <p class="empty">No configs exist — create them via the CLI.</p>
    {:else}
      <fieldset>
        <legend>Configs</legend>
        {#each configs as config (config)}
          <label class="check">
            <input type="checkbox" value={config} bind:group={chosen} />
            {config}
          </label>
        {/each}
      </fieldset>
    {/if}
    <div class="actions">
      <button type="button" onclick={() => (open = false)}>Cancel</button>
      <button
        type="submit"
        class="primary"
        disabled={submitting || $rateLimitSeconds > 0 || configs.length === 0}
      >
        {submitting ? 'Saving…' : 'Replace grants'}
      </button>
    </div>
  </form>
</dialog>

<style>
  h2 {
    margin: 0 0 0.5rem;
    font-size: 1.2rem;
  }

  fieldset {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 0.6rem 0.8rem;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    margin: 0;
  }

  legend {
    padding: 0 0.35rem;
    font-size: 0.85rem;
    color: var(--muted);
  }

  .check {
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }

  .error {
    padding: 0.5rem 0.7rem;
    border-radius: var(--radius);
    border: 1px solid var(--danger);
    background: var(--danger-bg);
    color: var(--danger-fg);
    margin: 0 0 0.75rem;
  }

  .empty,
  .hint {
    color: var(--muted);
    font-size: 0.9rem;
  }

  .hint {
    margin: 0 0 0.75rem;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    margin-top: 1rem;
  }
</style>
