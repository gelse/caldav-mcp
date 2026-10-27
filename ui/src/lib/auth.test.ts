import { describe, expect, it } from 'vitest';
import { ADMIN_KEY_STORAGE, clearKey, loadKey, saveKey } from './auth';

describe('sessionStorage key handling', () => {
  it('saveKey → loadKey round-trips under the fixed storage key', () => {
    saveKey('hunter2');
    expect(sessionStorage.getItem(ADMIN_KEY_STORAGE)).toBe('hunter2');
    expect(loadKey()).toBe('hunter2');
  });

  it('clearKey wipes storage and the in-memory mirror', () => {
    saveKey('hunter2');
    clearKey();
    expect(sessionStorage.getItem(ADMIN_KEY_STORAGE)).toBeNull();
    expect(loadKey()).toBeNull();
  });

  it('never touches localStorage', () => {
    // The three lines below deliberately *reference* localStorage to prove
    // it stays empty — the app itself is banned from it by the eslint
    // rules, and this is the only permitted shape (read-only emptiness).
    saveKey('hunter2');
    // eslint-disable-next-line no-restricted-globals -- assertion of emptiness only
    expect(localStorage.length).toBe(0);
    // eslint-disable-next-line no-restricted-globals -- assertion of emptiness only
    expect(localStorage.getItem(ADMIN_KEY_STORAGE)).toBeNull();
    clearKey();
    // eslint-disable-next-line no-restricted-globals -- assertion of emptiness only
    expect(localStorage.length).toBe(0);
  });

  it('loadKey returns null on a fresh session', () => {
    expect(loadKey()).toBeNull();
  });
});
