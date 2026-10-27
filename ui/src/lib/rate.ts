/**
 * Shared client-side rate-limit countdown (UI-M2 design (d): "429 during
 * any operation → forms disabled, Retry-After countdown shown, then
 * re-enabled"). One store, so a 429 received anywhere disables every form
 * at once until the countdown expires.
 */

import { writable } from 'svelte/store';

/** Seconds until retry is allowed; 0 = not rate-limited. */
export const rateLimitSeconds = writable(0);

let timer: ReturnType<typeof setInterval> | null = null;

/** Start (or restart) the countdown from a Retry-After value in seconds. */
export function triggerRateLimit(seconds: number): void {
  const initial = Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : 1;
  rateLimitSeconds.set(initial);
  if (timer !== null) clearInterval(timer);
  timer = setInterval(() => {
    rateLimitSeconds.update((current) => {
      if (current <= 1) {
        if (timer !== null) clearInterval(timer);
        timer = null;
        return 0;
      }
      return current - 1;
    });
  }, 1000);
}

/** Stop any running countdown and re-enable forms (test/teardown helper). */
export function stopRateLimit(): void {
  if (timer !== null) clearInterval(timer);
  timer = null;
  rateLimitSeconds.set(0);
}
