<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { ApiError, listUsers, type User } from './lib/api';
  import { clearKey, loadKey } from './lib/auth';
  import BannerList from './lib/components/BannerList.svelte';
  import GateView from './lib/components/GateView.svelte';
  import ToastList from './lib/components/ToastList.svelte';
  import UserDetailView from './lib/components/UserDetailView.svelte';
  import UsersView from './lib/components/UsersView.svelte';
  import {
    GATE_DISABLED_MESSAGE,
    GATE_EXPIRED_MESSAGE,
    gateOutcomeFor,
    type GateStatus,
  } from './lib/gate';
  import { isInsecureHttpOrigin } from './lib/origin';
  import { clearSignals } from './lib/toasts';

  type View = 'boot' | 'gate' | 'users' | 'detail';

  interface GateInitial {
    status: GateStatus;
    message: string;
    retryAfter: number;
    hasStoredKey: boolean;
  }

  let view = $state<View>('boot');
  let gateInitial = $state<GateInitial>({
    status: 'idle',
    message: '',
    retryAfter: 0,
    hasStoredKey: false,
  });
  let selectedUsername = $state<string | null>(null);
  // Users from the gate's successful validation — seeds the list view so
  // unlocking does not fetch GET /admin/users twice. Cleared whenever the
  // list is left or the session ends, so a return always re-fetches.
  let initialUsers = $state<User[] | null>(null);
  let httpWarningDismissed = $state(false);

  // Static per page load; the module is stubbed in tests because jsdom's
  // window.location cannot be reconfigured.
  const insecureOrigin = isInsecureHttpOrigin(location.href);
  const showHttpWarning = $derived(insecureOrigin && !httpWarningDismissed);

  async function focusHeading(): Promise<void> {
    await tick();
    const heading = document.querySelector('main h1');
    if (heading instanceof HTMLElement) heading.focus();
  }

  /** Enter the app after a successful key validation. */
  async function unlock(users: User[]): Promise<void> {
    initialUsers = users;
    view = 'users';
    await focusHeading();
  }

  /** Logout: wipe the key and all signal regions → gate. */
  function logout(): void {
    clearKey();
    clearSignals();
    initialUsers = null;
    gateInitial = { status: 'idle', message: '', retryAfter: 0, hasStoredKey: false };
    view = 'gate';
    void focusHeading();
  }

  /**
   * Central error router passed to the views: handles the two outcomes
   * that belong to the shell — a mid-session 401 (wipe + gate with "key no
   * longer valid", no retry loops) and a mid-session plain-text 404
   * (admin API disabled screen). Returns true when handled.
   */
  function routeError(error: ApiError): boolean {
    if (error.kind === 'auth') {
      clearKey();
      clearSignals();
      initialUsers = null;
      gateInitial = {
        status: 'expired',
        message: GATE_EXPIRED_MESSAGE,
        retryAfter: 0,
        hasStoredKey: false,
      };
      view = 'gate';
      void focusHeading();
      return true;
    }
    if (error.kind === 'disabled') {
      clearSignals();
      initialUsers = null;
      gateInitial = {
        status: 'disabled',
        message: GATE_DISABLED_MESSAGE,
        retryAfter: 0,
        hasStoredKey: loadKey() !== null,
      };
      view = 'gate';
      void focusHeading();
      return true;
    }
    return false;
  }

  async function selectUser(username: string): Promise<void> {
    selectedUsername = username;
    view = 'detail';
    await focusHeading();
  }

  async function backToList(): Promise<void> {
    selectedUsername = null;
    // The list refetches on return — anything may have changed in between.
    initialUsers = null;
    view = 'users';
    await focusHeading();
  }

  onMount(() => {
    const key = loadKey();
    if (key === null || key === '') {
      gateInitial = { status: 'idle', message: '', retryAfter: 0, hasStoredKey: false };
      view = 'gate';
      return;
    }
    // Silent re-validation of the stored key (design (c)).
    listUsers()
      .then((users) => {
        initialUsers = users;
        view = 'users';
      })
      .catch((error: unknown) => {
        const outcome = gateOutcomeFor(error, false);
        if (outcome.status === 'invalid') {
          // Stored key is wrong/expired — wipe it (design (c)).
          clearKey();
        }
        gateInitial = {
          status: outcome.status,
          message: outcome.message,
          retryAfter: outcome.retryAfter,
          hasStoredKey: loadKey() !== null,
        };
        view = 'gate';
      });
  });
</script>

<header class="app-header">
  <span class="brand">caldav-mcp <strong>admin</strong></span>
  {#if view === 'users' || view === 'detail'}
    <button type="button" class="logout" onclick={logout}>Log out</button>
  {/if}
</header>

<main>
  {#if showHttpWarning}
    <div class="http-warning" role="status">
      <span>
        Plain HTTP — send the admin key over TLS or localhost only; without TLS it crosses the
        network in plaintext.
      </span>
      <button
        type="button"
        class="dismiss"
        aria-label="Dismiss warning"
        onclick={() => (httpWarningDismissed = true)}>✕</button
      >
    </div>
  {/if}

  <BannerList />
  <ToastList />

  {#if view === 'boot'}
    <p class="boot" role="status">Checking saved key…</p>
  {:else if view === 'gate'}
    <GateView
      initialStatus={gateInitial.status}
      initialMessage={gateInitial.message}
      initialRetryAfter={gateInitial.retryAfter}
      hasStoredKey={gateInitial.hasStoredKey}
      onUnlock={unlock}
    />
  {:else if view === 'users'}
    <UsersView onSelect={selectUser} {routeError} initialUsers={initialUsers} />
  {:else if view === 'detail' && selectedUsername !== null}
    <UserDetailView username={selectedUsername} onBack={backToList} {routeError} />
  {/if}
</main>

<style>
  .app-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    padding: 0.8rem 1.25rem;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }

  .brand {
    font-size: 1.05rem;
    letter-spacing: 0.01em;
  }

  .logout {
    padding: 0.35rem 0.8rem;
  }

  main {
    padding: 1.25rem;
    max-width: 64rem;
    margin: 0 auto;
  }

  .http-warning {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.6rem 0.8rem;
    margin-bottom: 1rem;
    border-radius: var(--radius);
    border: 1px solid var(--warning-border);
    background: var(--warning-bg);
    color: var(--warning-fg);
  }

  .dismiss {
    border: none;
    background: none;
    color: inherit;
    cursor: pointer;
    padding: 0 0.2rem;
    line-height: 1;
  }

  .boot {
    color: var(--muted);
  }

  /* Responsive: tighter padding down to ~360 px (scoped so these beat the
     base rules above — flat selectors in app.css would lose the cascade). */
  @media (max-width: 700px) {
    main {
      padding: 1rem;
    }

    .app-header {
      padding: 0.7rem 1rem;
    }
  }
</style>
