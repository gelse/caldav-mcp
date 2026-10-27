<script lang="ts">
  import { banners, dismissBanner } from '../toasts';
</script>

{#each $banners as item (item.id)}
  <div class="banner" class:danger={item.tone === 'danger'} role="status" aria-live="polite">
    <span>{item.message}</span>
    <button
      type="button"
      class="dismiss"
      aria-label="Dismiss banner"
      onclick={() => dismissBanner(item.id)}>✕</button
    >
  </div>
{/each}

<style>
  .banner {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.6rem 0.8rem;
    border-radius: var(--radius);
    border: 1px solid var(--warning-border);
    background: var(--warning-bg);
    color: var(--warning-fg);
  }

  .banner.danger {
    border-color: var(--danger);
    background: var(--danger-bg);
    color: var(--danger-fg);
  }

  .dismiss {
    border: none;
    background: none;
    color: inherit;
    cursor: pointer;
    padding: 0 0.2rem;
    line-height: 1;
  }
</style>
