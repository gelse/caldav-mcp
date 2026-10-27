<script lang="ts">
  import { canGenerateKey, generateUserKey } from '../keygen';

  interface Props {
    id: string;
    label: string;
    value?: string;
  }

  let { id, label, value = $bindable('') }: Props = $props();

  let revealed = $state(false);
  let status = $state('');
  let statusSeq = 0;
  let statusTimer: ReturnType<typeof setTimeout> | undefined;

  // Generation only needs getRandomValues — available on secure and insecure
  // origins alike. Hidden solely when Web Crypto is missing entirely.
  const mayGenerate = canGenerateKey();
  // navigator.clipboard is unavailable on insecure origins — disable Copy
  // there instead of silently doing nothing (Reveal is the fallback).
  const mayCopy = typeof navigator !== 'undefined' && Boolean(navigator.clipboard);

  function announce(message: string): void {
    status = message;
    const seq = ++statusSeq;
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => {
      if (seq === statusSeq) status = '';
    }, 2000);
  }

  // The dialog remounts this field per open ({#key}) — never let a
  // status-clear timer outlive the component it belongs to.
  $effect(() => () => clearTimeout(statusTimer));

  function regenerate(): void {
    value = generateUserKey();
    revealed = false;
    announce('New key generated.');
  }

  async function copy(): Promise<void> {
    if (!navigator.clipboard || !value) return;
    try {
      await navigator.clipboard.writeText(value);
      announce('Copied to clipboard.');
    } catch {
      status = '';
    }
  }

  // Generated-only field: never typed. Browsers already block typing on a
  // read-only input; this re-syncs from state if an input event fires anyway
  // (scripted DOM, autofill), so what is displayed always equals the
  // generated key that will be submitted.
  function resync(event: Event): void {
    (event.currentTarget as HTMLInputElement).value = value;
  }
</script>

<div class="keyfield">
  <label for={id}>{label}</label>
  <div class="keyrow">
    <input
      {id}
      type={revealed ? 'text' : 'password'}
      value={value}
      oninput={resync}
      readonly
      autocomplete="off"
      spellcheck="false"
    />
    {#if mayGenerate}
      <button type="button" onclick={regenerate}>Regenerate</button>
    {/if}
    <button type="button" aria-pressed={revealed} onclick={() => (revealed = !revealed)}>
      {revealed ? 'Hide' : 'Reveal'}
    </button>
    <button type="button" onclick={copy} disabled={!value || !mayCopy}>Copy</button>
  </div>
  <p class="hint" role="status" aria-live="polite">
    {status}
  </p>
</div>

<style>
  .keyfield {
    display: flex;
    flex-direction: column;
    gap: 0.35rem;
  }

  .keyrow {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
  }

  .keyrow input {
    flex: 1 1 12rem;
    min-width: 0;
  }

  .hint {
    margin: 0;
    min-height: 1.2em;
    font-size: 0.85rem;
    color: var(--muted);
  }
</style>
