/**
 * Plain-HTTP transport warning helper (UI-M2 design (e)) — mirrors the
 * "Transport warning — read before sending the key" section of
 * docs/api.md: the admin key must be sent over TLS or localhost only.
 *
 * Pure so the conditional is unit-testable (jsdom's window.location is
 * not configurable, so the component tests stub this module instead).
 */
export function isInsecureHttpOrigin(href: string): boolean {
  let url: URL;
  try {
    url = new URL(href);
  } catch {
    return false;
  }
  if (url.protocol !== 'http:') return false;
  const host = url.hostname.toLowerCase();
  if (host === 'localhost' || host === '127.0.0.1' || host === '::1' || host === '[::1]') {
    return false;
  }
  if (host.endsWith('.localhost')) return false;
  return true;
}
