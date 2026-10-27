import { describe, expect, it, vi } from 'vitest';
import { canGenerateKey, generateUserKey } from './keygen';

describe('canGenerateKey', () => {
  it('is true on insecure origins: getRandomValues without crypto.subtle', () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (array: Uint8Array) => array,
    });
    expect(canGenerateKey()).toBe(true);
  });

  it('is false when getRandomValues is missing', () => {
    vi.stubGlobal('crypto', {});
    expect(canGenerateKey()).toBe(false);
  });

  it('is false when crypto is missing entirely', () => {
    vi.stubGlobal('crypto', undefined);
    expect(canGenerateKey()).toBe(false);
  });
});

describe('generateUserKey', () => {
  it('draws from crypto.getRandomValues — 24 bytes as 48 lowercase hex characters', () => {
    const getRandomValues = vi.fn((array: Uint8Array) => {
      array.fill(0xab);
      return array;
    });
    vi.stubGlobal('crypto', { getRandomValues });

    const key = generateUserKey();

    expect(getRandomValues).toHaveBeenCalledTimes(1);
    expect(getRandomValues).toHaveBeenCalledWith(expect.any(Uint8Array));
    expect(key).toBe('ab'.repeat(24));
    expect(key).toMatch(/^[0-9a-f]{48}$/);
  });

  it('returns a fresh value on every call', () => {
    const keys = Array.from({ length: 10 }, () => generateUserKey());
    expect(new Set(keys).size).toBe(keys.length);
    for (const key of keys) expect(key).toMatch(/^[0-9a-f]{48}$/);
  });
});
