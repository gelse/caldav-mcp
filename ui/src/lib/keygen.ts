/**
 * User-key generation for the create/rotate dialogs (UI-M2 design (d)).
 *
 * Keys are generated-only: the operator never types one. Generation uses
 * `crypto.getRandomValues`, which browsers expose on secure *and* insecure
 * origins alike (unlike `crypto.subtle`), so the affordance is available
 * wherever the app runs. `canGenerateKey()` only guards against Web Crypto
 * being missing entirely.
 */

export function canGenerateKey(): boolean {
  return typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function';
}

/** 24 random bytes as lowercase hex — 48 characters. */
export function generateUserKey(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}
