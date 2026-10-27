import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// Admin SPA build/dev config (UI-M2).
//
// - `base: "/ui/"` so the built index.html references `/ui/assets/…` —
//   required for UI-M3 hosting under the Python server.
// - Dev proxy: same-origin `/admin/*` requests go to a running pro-mode
//   server (default: docker-compose host port). Override with
//   CALDAV_MCP_DEV_PROXY (bare local server: http://localhost:8080).
// - Tests run in jsdom; `fetch` is stubbed per test (no network).
export default defineConfig({
  base: '/ui/',
  plugins: [svelte()],
  // Under vitest, resolve `svelte` (and friends) to their browser entries —
  // otherwise the package's server build is loaded and `mount()` is
  // unavailable in jsdom.
  resolve: process.env.VITEST ? { conditions: ['browser'] } : undefined,
  server: {
    proxy: {
      '/admin': process.env.CALDAV_MCP_DEV_PROXY ?? 'http://localhost:8600',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
  },
});
