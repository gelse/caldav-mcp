import { describe, expect, it } from 'vitest';
import { isInsecureHttpOrigin } from './origin';

describe('isInsecureHttpOrigin', () => {
  it('warns on plain HTTP to a non-local host', () => {
    expect(isInsecureHttpOrigin('http://192.168.1.10:8600/ui/')).toBe(true);
    expect(isInsecureHttpOrigin('http://nas.local:8080/ui/')).toBe(true);
  });

  it('stays silent on localhost variants', () => {
    expect(isInsecureHttpOrigin('http://localhost:8600/ui/')).toBe(false);
    expect(isInsecureHttpOrigin('http://127.0.0.1:8600/ui/')).toBe(false);
    expect(isInsecureHttpOrigin('http://[::1]:8600/ui/')).toBe(false);
    expect(isInsecureHttpOrigin('http://machine.localhost:8600/ui/')).toBe(false);
  });

  it('stays silent on HTTPS and on unparseable input', () => {
    expect(isInsecureHttpOrigin('https://example.com/ui/')).toBe(false);
    expect(isInsecureHttpOrigin('not a url')).toBe(false);
  });
});
