/**
 * User-key generation for the create/rotate dialogs (UI-M2 design (d)).
 *
 * The generate affordance is only shown where `crypto.subtle` exists —
 * browsers expose it on secure origins only, so on an insecure LAN HTTP
 * origin the button is hidden and the operator types or pastes a key.
 */

export function canGenerateKey(): boolean {
  return typeof crypto !== 'undefined' && typeof crypto.subtle !== 'undefined';
}

/** 24 random bytes as lowercase hex — 48 characters. */
export function generateUserKey(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}
