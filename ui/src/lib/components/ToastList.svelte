<script lang="ts">
  import { dismissToast, toasts } from '../toasts';
</script>

<!-- ARIA live region: toasts are announced politely, errors as alerts. -->
<div class="toasts" role="status" aria-live="polite">
  {#each $toasts as item (item.id)}
    <div class="toast" class:error={item.kind === 'error'} role={item.kind === 'error' ? 'alert' : 'status'}>
      <span>{item.message}</span>
      <button
        type="button"
        class="dismiss"
        aria-label="Dismiss notification"
        onclick={() => dismissToast(item.id)}>✕</button
      >
    </div>
  {/each}
</div>

<style>
  .toasts {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .toast {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.6rem 0.8rem;
    border-radius: var(--radius);
    border: 1px solid var(--border);
    background: var(--surface);
    box-shadow: var(--shadow);
  }

  .toast.error {
    border-color: var(--danger);
  }

  .dismiss {
    border: none;
    background: none;
    color: var(--muted);
    cursor: pointer;
    padding: 0 0.2rem;
    line-height: 1;
  }

  .dismiss:hover {
    color: var(--text);
  }
</style>
