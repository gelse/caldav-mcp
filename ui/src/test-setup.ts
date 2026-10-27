import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/svelte';
import { afterEach, vi } from 'vitest';
import { clearKey } from './lib/auth';
import { stopRateLimit } from './lib/rate';
import { banners, toasts } from './lib/toasts';

// jsdom does not always ship a working modal <dialog> API; polyfill just
// enough (showModal/close/open) for the native-dialog components under test.
if (
  typeof HTMLDialogElement !== 'undefined' &&
  typeof HTMLDialogElement.prototype.showModal !== 'function'
) {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement): void {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement): void {
    if (!this.hasAttribute('open')) return;
    this.removeAttribute('open');
    this.dispatchEvent(new Event('close'));
  };
}

// jsdom's window.crypto has getRandomValues but no `subtle`; the app hides
// the generate-key affordance when `subtle` is absent (secure-origin proxy),
// so provide a stub to make the affordance testable. Individual tests can
// still vi.stubGlobal('crypto', …) to simulate an insecure origin.
if (typeof globalThis.crypto !== 'undefined' && !globalThis.crypto.subtle) {
  Object.defineProperty(globalThis.crypto, 'subtle', {
    value: {},
    configurable: true,
  });
}

afterEach(() => {
  cleanup();
  clearKey(); // resets sessionStorage AND the auth module's memory mirror
  // localStorage is deliberately never cleared here because nothing may
  // ever write to it — tests assert localStorage.length stays 0 (the ban
  // itself is enforced by the eslint rules).
  vi.unstubAllGlobals();
  // jsdom has no navigator.clipboard — drop any per-test stub/override.
  delete (navigator as { clipboard?: Clipboard }).clipboard;
  toasts.set([]);
  banners.set([]);
  stopRateLimit();
});
