/**
 * sessionStorage-backed admin-key storage (UI-M2 design (c)).
 *
 * This is the only module in the app that touches browser storage — a
 * single-storage-point so the "no localStorage, ever" rule is enforceable
 * by review and by the eslint bans in eslint.config.js. The key never
 * appears in a URL, the history API, or a log statement.
 *
 * sessionStorage semantics: the key is cleared when the tab closes and is
 * never shared across tabs. The small in-memory mirror only covers
 * storage-disabled environments (quota/private modes) for the current
 * session; clearKey() resets both so tests and logout stay deterministic.
 */

export const ADMIN_KEY_STORAGE = 'caldav-mcp-admin-key';

let memoryKey: string | null = null;

/** Read the stored admin key (memory first, then sessionStorage). */
export function loadKey(): string | null {
  if (memoryKey !== null) return memoryKey;
  try {
    memoryKey = sessionStorage.getItem(ADMIN_KEY_STORAGE);
  } catch {
    memoryKey = null;
  }
  return memoryKey;
}

/** Persist the admin key for this tab's session. */
export function saveKey(key: string): void {
  memoryKey = key;
  try {
    sessionStorage.setItem(ADMIN_KEY_STORAGE, key);
  } catch {
    // Storage unavailable — the in-memory copy still serves this session.
  }
}

/** Wipe the admin key (logout, mid-session 401, failed gate validation). */
export function clearKey(): void {
  memoryKey = null;
  try {
    sessionStorage.removeItem(ADMIN_KEY_STORAGE);
  } catch {
    // Nothing stored / storage unavailable — memory already cleared.
  }
}
