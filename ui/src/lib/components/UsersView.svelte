<script lang="ts">
  import { onMount } from 'svelte';
  import { ApiError, listUsers, type User } from '../api';
  import { rateLimitSeconds, triggerRateLimit } from '../rate';
  import CreateUserDialog from './CreateUserDialog.svelte';

  interface Props {
    onSelect: (username: string) => void;
    routeError?: (error: ApiError) => boolean;
    /** Fresh payload from the gate's validation — skips the initial fetch. */
    initialUsers?: User[] | null;
  }

  let { onSelect, routeError = () => false, initialUsers = null }: Props = $props();

  let users = $state<User[]>([]);
  let loading = $state(false);
  let loadMessage = $state('');
  let loadFailed = $state(false);
  let filter = $state('');
  let createOpen = $state(false);

  const visible = $derived(
    users.filter((user) => {
      const query = filter.trim().toLowerCase();
      if (query === '') return true;
      return (
        user.username.toLowerCase().includes(query) ||
        user.configs.some((config) => config.toLowerCase().includes(query))
      );
    }),
  );

  async function refresh(): Promise<void> {
    loading = true;
    loadFailed = false;
    loadMessage = '';
    try {
      users = await listUsers();
    } catch (error) {
      if (error instanceof ApiError) {
        if (routeError(error)) return;
        if (error.kind === 'ratelimited') {
          triggerRateLimit(error.retryAfterSeconds ?? 5);
          loadMessage = 'Rate limited — wait for the countdown, then refresh.';
        } else {
          loadMessage = error.message;
        }
        loadFailed = true;
      } else {
        loadMessage = 'Something went wrong — try again.';
        loadFailed = true;
      }
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    if (initialUsers !== null) {
      users = initialUsers;
      return;
    }
    void refresh();
  });
</script>

<h1 tabindex="-1">Users</h1>

<div class="toolbar">
  <div class="field">
    <label for="user-filter">Filter</label>
    <input
      id="user-filter"
      type="search"
      placeholder="Username or config name"
      bind:value={filter}
      autocomplete="off"
    />
  </div>
  <button
    type="button"
    onclick={refresh}
    disabled={loading || $rateLimitSeconds > 0 || createOpen}>Refresh</button
  >
  <button
    type="button"
    class="primary"
    onclick={() => (createOpen = true)}
    disabled={$rateLimitSeconds > 0}>Add user</button
  >
</div>

{#if $rateLimitSeconds > 0}
  <p class="rate-note" role="status">Rate limited — retry in {$rateLimitSeconds}s.</p>
{/if}

{#if loadFailed}
  <p class="error" role="alert">{loadMessage}</p>
{/if}

{#if loading && users.length === 0}
  <p class="empty">Loading users…</p>
{:else if !loadFailed && users.length === 0}
  <p class="empty">No users yet — add one.</p>
{:else if users.length > 0 && visible.length === 0}
  <p class="empty">No users match “{filter.trim()}”.</p>
{:else}
  <ul class="user-list">
    {#each visible as user (user.username)}
      <li>
        <button type="button" class="user-row" onclick={() => onSelect(user.username)}>
          <span class="username">{user.username}</span>
          <span class="chips">
            {#each user.configs as config (config)}
              <span class="chip">{config}</span>
            {/each}
            {#if user.configs.length === 0}
              <span class="chip muted">no configs</span>
            {/if}
          </span>
        </button>
      </li>
    {/each}
  </ul>
{/if}

<CreateUserDialog
  bind:open={createOpen}
  onCreated={refresh}
  {routeError}
/>

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

  .toolbar {
    display: flex;
    gap: 0.6rem;
    align-items: flex-end;
    flex-wrap: wrap;
    margin-bottom: 1rem;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    flex: 1 1 14rem;
    min-width: 0;
  }

  .rate-note {
    color: var(--muted);
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

  .empty {
    color: var(--muted);
    padding: 1rem 0;
  }

  .user-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 0.6rem;
    grid-template-columns: repeat(auto-fill, minmax(18rem, 1fr));
  }

  .user-row {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 0.45rem;
    align-items: flex-start;
    text-align: left;
    padding: 0.8rem 0.9rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
    cursor: pointer;
    box-shadow: var(--shadow);
  }

  .user-row:hover {
    border-color: var(--accent);
  }

  .username {
    font-weight: 600;
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 0.35rem;
  }

  .chip {
    font-size: 0.8rem;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    background: var(--chip-bg);
    border: 1px solid var(--border);
  }

  .chip.muted {
    color: var(--muted);
    background: none;
  }
</style>
