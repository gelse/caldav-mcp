# Admin UI (`ui/`)

The admin UI is a single-page app for the pro-mode admin REST API: unlock it with the admin key, then list users, create or delete users, grant or revoke configs, and rotate keys. It talks only to the same `/admin/*` endpoints documented in [`docs/api.md`](api.md) — same origin, no other backend.

This page covers local development. The UI is **not yet served by the Python server**; hosting is added in a follow-up change (see [Hosting](#hosting)).

## Prerequisites

- **Node.js >= 20.19 with npm** — required by Vite 7 and enforced by every `make ui-*` target.
- **A pro-mode server with `CALDAV_MCP_ADMIN_KEY` set** — the UI is a client of the admin API; without it there is nothing to talk to.

Everything else installs from `ui/package.json`. The app itself ships **zero runtime dependencies** — the build output is plain HTML, CSS, and JavaScript, with no CDN or font requests, so it works offline and behind a strict `default-src 'self'` CSP.

## Development workflow

```bash
cd ui
npm ci          # first checkout (or after package-lock.json changes)
npm run dev     # Vite dev server, default http://localhost:5173/ui/
```

The dev server proxies `/admin/*` requests to a running backend, so the browser only ever sees one origin. The proxy target defaults to `http://localhost:8600` (the bundled compose file's host port) and is overridden with `CALDAV_MCP_DEV_PROXY` — for a bare local server use:

```bash
CALDAV_MCP_DEV_PROXY=http://localhost:8080 npm run dev
```

**The proxy target must be a pro-mode server** (`DB_CONFIG_ENABLED=true`) **with `CALDAV_MCP_ADMIN_KEY` set** — for example the test compose file's `mcp-pro` service on port 8080, or a local pro-mode run. Against any other server (simple mode, admin key unset) the app correctly shows its **“admin API not available”** screen — that screen means the server is not in pro mode, not that your key is wrong.

## Commands

```bash
npm run test        # vitest (jsdom, fetch stubbed — no network)
npm run test -- --run   # single run instead of watch mode
npm run lint        # eslint (incl. localStorage and inline-style bans)
npm run typecheck   # svelte-check (TypeScript)
npm run build       # production build → ui/dist, then the post-build
                    # CSP-construction check (npm run check:csp): no inline
                    # <script>, no style=, /ui/ base intact
```

From the repository root the same gates run as `make ui-install ui-build ui-test ui-lint ui-typecheck`, aggregated by `make ui-check` (which includes `ui-build`, so the CSP check runs), which is part of `make check`.

## Code map

| Path | Role |
|------|------|
| `src/lib/api.ts` | The only I/O boundary: typed client for all admin routes, `ApiError` kinds (400/401/404 JSON-vs-plain-text/409/429+Retry-After/500/503), success-signal mapping |
| `src/lib/auth.ts` | The only module that touches browser storage — sessionStorage-backed admin key |
| `src/lib/gate.ts` | Maps validation failures onto the gate screens |
| `src/lib/toasts.ts` | Toast/banner stores; routes `reload: "failed"` and topology-restart signals to banners |
| `src/lib/rate.ts` | Shared 429 countdown that disables every form at once |
| `src/App.svelte` | Shell: key gate ↔ user list ↔ user detail (no client-side router), logout, plain-HTTP warning |
| `src/lib/components/` | Views and native-`<dialog>` flows (create, replace, rotate, delete) |
| `*.test.ts` | Co-located vitest suites (`fetch` stubbed via `vi.stubGlobal`) |

There is deliberately **no client-side router**: three views, no shareable URLs — refresh always returns to the gate, which re-validates the stored key silently. Deep links are not supported.

## Security hygiene

- **The admin key lives in `sessionStorage` only** (`caldav-mcp-admin-key`), never in `localStorage`, a URL, or browser history. Closing the tab clears it; **Log out** wipes it explicitly.
- A **mid-session 401** (key rotated or invalidated elsewhere) wipes the key and returns to the gate with “key no longer valid” — no retry loops.
- **Keys are never displayed from server responses** — the API returns none. New keys are shown **only at create/rotate time**, with reveal and copy affordances, because the server stores only the hash and the value cannot be recovered later; copy it before closing the dialog.
- **Nothing is logged**: request and response bodies can carry key material, so the app contains no `console` output (eslint-enforced).
- **Plain-HTTP warning**: on a non-localhost `http://` origin the app warns that the admin key crosses the network in plaintext — send it over TLS or localhost only (same rule as [`docs/api.md`](api.md#transport-warning--read-before-sending-the-key)).
- **No inline styles anywhere** (eslint ban + post-build grep): component styling compiles through classes and CSS custom properties, keeping the built `index.html` compatible with a strict CSP.

## Browser support

Evergreen browsers (current Chrome/Edge/Firefox/Safari). The app needs ES2022 modules, `sessionStorage`, native `<dialog>`, and Web Crypto; the **Generate** button is hidden on insecure origins where `crypto.subtle` is unavailable (type or paste a key there instead). Dark mode follows `prefers-color-scheme`; there is no manual toggle.

## Known limitations

- Refreshing the page returns you to the gate (see above) — there are no deep links.
- Two admins rotating the same key concurrently: last write wins (both requests succeed).
- Config **management** stays CLI-only; the UI only lists and grants config names.

## Hosting

The Python server does not serve this app yet. Local development uses the Vite dev server above; serving the built `ui/dist` from the server under `/ui` (behind `SHOW_UI`) is added in the follow-up milestone, which will also rewrite this section.
