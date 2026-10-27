<script lang="ts">
  import { canGenerateKey, generateUserKey } from '../keygen';

  interface Props {
    id: string;
    label: string;
    value?: string;
  }

  let { id, label, value = $bindable('') }: Props = $props();

  let revealed = $state(false);
  let copied = $state(false);

  // Only offered on secure origins (crypto.subtle); hidden otherwise.
  const mayGenerate = canGenerateKey();
  // navigator.clipboard is unavailable on insecure origins — disable Copy
  // there instead of silently doing nothing (Reveal is the fallback).
  const mayCopy = typeof navigator !== 'undefined' && Boolean(navigator.clipboard);

  async function copy(): Promise<void> {
    if (!navigator.clipboard || !value) return;
    try {
      await navigator.clipboard.writeText(value);
      copied = true;
      setTimeout(() => (copied = false), 2000);
    } catch {
      copied = false;
    }
  }
</script>

<div class="keyfield">
  <label for={id}>{label}</label>
  <div class="keyrow">
    <input
      {id}
      type={revealed ? 'text' : 'password'}
      bind:value
      autocomplete="off"
      spellcheck="false"
    />
    {#if mayGenerate}
      <button type="button" onclick={() => (value = generateUserKey())}>Generate</button>
    {/if}
    <button type="button" aria-pressed={revealed} onclick={() => (revealed = !revealed)}>
      {revealed ? 'Hide' : 'Reveal'}
    </button>
    <button type="button" onclick={copy} disabled={!value || !mayCopy}>Copy</button>
  </div>
  <p class="hint" role="status" aria-live="polite">
    {copied ? 'Copied to clipboard.' : ''}
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
