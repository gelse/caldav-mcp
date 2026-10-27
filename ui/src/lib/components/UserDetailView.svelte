<script lang="ts">
  import { onMount } from 'svelte';
  import {
    ApiError,
    getUser,
    grantConfig,
    listConfigs,
    revokeConfig,
    type User,
  } from '../api';
  import { rateLimitSeconds, triggerRateLimit } from '../rate';
  import { reportMutation, toast } from '../toasts';
  import DeleteUserDialog from './DeleteUserDialog.svelte';
  import ReplaceConfigsDialog from './ReplaceConfigsDialog.svelte';
  import RotateKeyDialog from './RotateKeyDialog.svelte';

  interface Props {
    username: string;
    onBack: () => void;
    routeError?: (error: ApiError) => boolean;
  }

  let { username, onBack, routeError = () => false }: Props = $props();

  type ConfigsState = 'idle' | 'loading' | 'ok' | 'empty' | 'disabled' | 'error';

  let user = $state<User | null>(null);
  let loading = $state(true);
  let loadMessage = $state('');
  let loadFailed = $state(false);

  let knownConfigs = $state<string[]>([]);
  let configsState = $state<ConfigsState>('idle');
  let configsMessage = $state('');
  let selectedConfig = $state('');
  let grantError = $state('');
  let grantBusy = $state(false);
  let revokeBusy = $state(false);

  let replaceOpen = $state(false);
  let rotateOpen = $state(false);
  let deleteOpen = $state(false);

  const busy = $derived(grantBusy || revokeBusy);
  const actionsDisabled = $derived(busy || $rateLimitSeconds > 0 || !user);

  async function loadUser(): Promise<void> {
    loading = true;
    loadFailed = false;
    loadMessage = '';
    try {
      user = await getUser(username);
    } catch (error) {
      if (error instanceof ApiError) {
        // Stale detail view: user gone → toast + back to the list (design (d)).
        if (error.kind === 'notfound') {
          toast(error.message, 'error');
          onBack();
          return;
        }
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

  async function loadConfigs(): Promise<void> {
    configsState = 'loading';
    configsMessage = '';
    try {
      knownConfigs = await listConfigs();
      configsState = knownConfigs.length === 0 ? 'empty' : 'ok';
      if (selectedConfig !== '' && !knownConfigs.includes(selectedConfig)) {
        selectedConfig = '';
      }
    } catch (error) {
      if (error instanceof ApiError) {
        // Fixed decision (d): a plain-text 404 here degrades just this
        // dropdown to an explanatory note — the shell's disabled screen
        // still applies to every other call.
        if (error.kind === 'disabled') {
          configsState = 'disabled';
          return;
        }
        if (routeError(error)) return;
        if (error.kind === 'ratelimited') {
          triggerRateLimit(error.retryAfterSeconds ?? 5);
          configsMessage = 'Rate limited — wait for the countdown, then refresh.';
        } else {
          configsMessage = error.message;
        }
        configsState = 'error';
      } else {
        configsMessage = 'Could not load configs — try again.';
        configsState = 'error';
      }
    }
  }

  async function grant(): Promise<void> {
    const config = selectedConfig;
    if (config === '' || grantBusy || $rateLimitSeconds > 0) return;
    grantBusy = true;
    grantError = '';
    try {
      const updated = await grantConfig(username, config);
      user = updated;
      reportMutation(updated, `Granted “${config}” — applied live.`);
      selectedConfig = '';
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.kind === 'notfound') {
          toast(error.message, 'error');
          onBack();
          return;
        }
        if (routeError(error)) return;
        if (error.kind === 'ratelimited') {
          triggerRateLimit(error.retryAfterSeconds ?? 5);
        }
        if (error.kind === 'validation' || error.kind === 'conflict') {
          grantError = error.message;
        } else {
          toast(error.message, 'error');
        }
      } else {
        toast('Something went wrong — try again.', 'error');
      }
    } finally {
      grantBusy = false;
    }
  }

  async function revoke(config: string): Promise<void> {
    if (revokeBusy || $rateLimitSeconds > 0) return;
    revokeBusy = true;
    try {
      const result = await revokeConfig(username, config);
      if (user !== null) {
        user = { ...user, configs: user.configs.filter((name) => name !== config) };
      }
      // 200 is idempotent — revoking a never-granted config still reports.
      reportMutation(result, `Revoked “${config}” — applied live.`);
    } catch (error) {
      if (error instanceof ApiError) {
        if (error.kind === 'notfound') {
          toast(error.message, 'error');
          onBack();
          return;
        }
        if (routeError(error)) return;
        if (error.kind === 'ratelimited') {
          triggerRateLimit(error.retryAfterSeconds ?? 5);
        }
        toast(error.message, 'error');
      } else {
        toast('Something went wrong — try again.', 'error');
      }
    } finally {
      revokeBusy = false;
    }
  }

  function replaced(updated: User): void {
    user = updated;
    reportMutation(updated, 'Config grants replaced — applied live.');
  }

  function rotated(body: unknown): void {
    // The dialog shows its own success state; only signal-field banners
    // are surfaced here (applied-live is implied by the success panel).
    reportMutation(body, null);
  }

  function deleted(body: unknown): void {
    reportMutation(body, `User “${username}” deleted — applied live.`);
    onBack();
  }

  /** Stale detail view (user gone): surface the server's message, leave. */
  function staleNotFound(error: ApiError): void {
    toast(error.message, 'error');
    onBack();
  }

  onMount(() => {
    void loadUser();
    void loadConfigs();
  });
</script>

<div class="head-row">
  <button type="button" onclick={onBack}>← Users</button>
  <h1 tabindex="-1">{username}</h1>
</div>

{#if loading}
  <p class="empty">Loading user…</p>
{:else if loadFailed}
  <p class="error" role="alert">{loadMessage}</p>
  <button type="button" onclick={loadUser}>Try again</button>
{:else if user}
  <section class="card" aria-labelledby="configs-heading">
    <h2 id="configs-heading">Config grants</h2>
    {#if user.configs.length === 0}
      <p class="empty">No configs granted.</p>
    {:else}
      <ul class="chips">
        {#each user.configs as config (config)}
          <li class="chip">
            {config}
            <button
              type="button"
              class="chip-x"
              aria-label="Revoke {config}"
              disabled={actionsDisabled}
              onclick={() => void revoke(config)}>✕</button
            >
          </li>
        {/each}
      </ul>
    {/if}

    <div class="grant-row">
      {#if configsState === 'disabled'}
        <p class="note" role="status">
          Config discovery is unavailable — the admin API is not enabled on this server.
        </p>
      {:else if configsState === 'empty'}
        <p class="note" role="status">No configs exist — create them via the CLI.</p>
      {:else if configsState === 'error'}
        <p class="error" role="alert">{configsMessage}</p>
        <button type="button" onclick={loadConfigs}>Retry</button>
      {:else}
        <div class="field">
          <label for="grant-config">Grant config</label>
          <select
            id="grant-config"
            bind:value={selectedConfig}
            disabled={configsState === 'loading' || actionsDisabled}
          >
            <option value="" disabled={configsState !== 'loading'}>
              {configsState === 'loading' ? 'Loading configs…' : 'Choose a config…'}
            </option>
            {#each knownConfigs as config (config)}
              <option value={config}>{config}</option>
            {/each}
          </select>
        </div>
        <button
          type="button"
          onclick={loadConfigs}
          disabled={configsState === 'loading' || actionsDisabled}>Refresh configs</button
        >
        <button
          type="button"
          class="primary"
          onclick={grant}
          disabled={selectedConfig === '' || actionsDisabled}
        >
          {grantBusy ? 'Granting…' : 'Grant'}
        </button>
      {/if}
    </div>
    {#if grantError}
      <p class="error" role="alert">{grantError}</p>
    {/if}
  </section>

  {#if $rateLimitSeconds > 0}
    <p class="rate-note" role="status">Rate limited — retry in {$rateLimitSeconds}s.</p>
  {/if}

  <section class="card" aria-labelledby="manage-heading">
    <h2 id="manage-heading">Manage user</h2>
    <div class="actions">
      <button type="button" onclick={() => (replaceOpen = true)} disabled={actionsDisabled || (configsState !== 'ok' && configsState !== 'empty')}>
        Replace set…
      </button>
      <button type="button" onclick={() => (rotateOpen = true)} disabled={actionsDisabled}>
        Rotate key…
      </button>
      <button type="button" class="danger" onclick={() => (deleteOpen = true)} disabled={actionsDisabled}>
        Delete user…
      </button>
    </div>
  </section>

  <ReplaceConfigsDialog
    bind:open={replaceOpen}
    {username}
    configs={knownConfigs}
    current={user.configs}
    onReplaced={replaced}
    onNotFound={staleNotFound}
    {routeError}
  />
  <RotateKeyDialog
    bind:open={rotateOpen}
    {username}
    onRotated={rotated}
    onNotFound={staleNotFound}
    {routeError}
  />
  <DeleteUserDialog
    bind:open={deleteOpen}
    {username}
    onDeleted={deleted}
    onNotFound={staleNotFound}
    {routeError}
  />
{/if}

<style>
  .head-row {
    display: flex;
    align-items: center;
    gap: 0.9rem;
    margin-bottom: 1rem;
  }

  h1 {
    margin: 0;
    font-size: 1.5rem;
    overflow-wrap: anywhere;
  }

  h1:focus {
    outline: none;
  }

  h1:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  h2 {
    margin: 0 0 0.75rem;
    font-size: 1.1rem;
  }

  .card {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: var(--surface);
    padding: 1rem;
    margin-bottom: 1rem;
    box-shadow: var(--shadow);
  }

  .chips {
    list-style: none;
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin: 0 0 1rem;
    padding: 0;
  }

  .chip {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    font-size: 0.85rem;
    padding: 0.15rem 0.35rem 0.15rem 0.6rem;
    border-radius: 999px;
    background: var(--chip-bg);
    border: 1px solid var(--border);
  }

  .chip-x {
    border: none;
    background: none;
    color: var(--muted);
    cursor: pointer;
    padding: 0 0.25rem;
    line-height: 1;
    border-radius: 999px;
  }

  .chip-x:hover:not(:disabled) {
    color: var(--danger);
  }

  .grant-row {
    display: flex;
    gap: 0.6rem;
    align-items: flex-end;
    flex-wrap: wrap;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
    flex: 1 1 14rem;
    min-width: 0;
  }

  .note {
    color: var(--muted);
    margin: 0;
  }

  .empty {
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

  .rate-note {
    color: var(--muted);
    margin: 0 0 0.75rem;
  }

  .actions {
    display: flex;
    gap: 0.6rem;
    flex-wrap: wrap;
  }
</style>
