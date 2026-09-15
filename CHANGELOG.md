# Changelog

## M5 — Partial-failure reporting and documentation

### Features
- **Partial-failure reporting (M5.1):** Read-tool fan-out now reports per-remote statuses (`ok`, `empty`, `auth`, `error`, `not_found`) and renders bracketed per-remote detail lines in the `message` field. One remote's failure never masks other remotes' results.
- **Multi-remote integration tests (M5.2):** Two-Radicale-server integration suite covering fan-out, passthrough, header/env precedence, and bad-credentials edge cases.
- **Read-only mode:** `CALDAV_MCP_READ_ONLY` flag hides write tools at registration time. In read-only mode only 8 read-only tools are visible to MCP clients.

### Features (M1–M4)
- **M1 header-precedence rule:** CalDAV credential resolution is mode-based — when `CALDAV_URL` is set, `X-Caldav-*` request headers are ignored entirely (no per-field mixing). When `CALDAV_URL` is unset, all three `X-Caldav-*` headers are required per request.
- **Config singleton:** Read-only `AppConfig` as the single source of CalDAV connection data; env/header/db modes.
- **Pro mode (M4):** `DB_CONFIG_ENABLED=true` loads config and users from the SQLite store at startup. DB-user auth (`X-Mcp-Username` + Bearer/`X-Api-Key`), fan-out reads across remotes, dotted-path write addressing (`config.remote.calendar`).
- **Config store and CLI (M3):** SQLite-backed store with `caldav-mcp-config` CLI for managing users, configs, remotes, and calendars. Fernet encryption of CalDAV passwords at rest (`CALDAV_MCP_CONFIG_SECRET`).
- **Partial-failure reporting (M5.1):** Per-remote status classification, `render_fanout_message` with bracketed detail lines, severity-based aggregation for mixed-outcome remotes.

### Changed
- **Breaking (M1):** CalDAV credential resolution is now mode-based. If `CALDAV_URL` is set, all credentials come from the environment and `X-Caldav-*` request headers are ignored (previously headers took precedence per-field). If `CALDAV_URL` is unset, the three `X-Caldav-*` headers are required per request. No per-field mixing. `X-Caldav-Username`/`X-Caldav-Password` are reserved for a future passthrough mode.
- **Breaking (M4):** `CALDAV_MCP_API_KEY` is ignored in pro mode (`DB_CONFIG_ENABLED=true`). Only DB-stored user credentials are accepted.

### New environment variables
- `DB_CONFIG_ENABLED` — enable pro mode (default: `false`)
- `CALDAV_MCP_DB_PATH` — path to the SQLite configuration store (default: `""`)
- `CALDAV_MCP_CONFIG_SECRET` — master secret for encrypting CalDAV credentials at rest (default: `""`)

### Upgrade notes
- **M1 header-precedence change:** Clients that previously sent `X-Caldav-*` headers while `CALDAV_URL` was set now get environment credentials instead of header credentials. If you relied on headers overriding env vars, remove `CALDAV_URL` and use header mode.
- **Pro mode:** `CALDAV_MCP_API_KEY` is ignored when `DB_CONFIG_ENABLED=true`. Use `X-Mcp-Username` + DB-stored key instead.
- **Restart-to-apply:** Configuration changes (env vars, store modifications) require a server restart. There is no hot reload.

## v0.1.1 (2026-09-11)

### Fixed
- DAVClient SSL verify kwarg (`ssl_verify` → `ssl_verify_cert`) so `CALDAV_MCP_CALDAV_VERIFY_SSL=false` works (#8)

### Changed
- CI: Docker image now tagged `testing` for the testing branch; workflow permissions fix (#9)
- Docs: improved `caldav_search_events` tool docstring to guide callers

## v0.1.0 (2026-08-21)

### Features
- 14 MCP tools for CalDAV calendar operations
- Streamable HTTP transport (no stdio)
- Two-layer authentication (API key + CalDAV credentials)
- Per-request multi-account support via HTTP headers
- Thread-safe LRU client cache (max 8, 1h TTL)
- Per-IP rate limiting with exponential backoff
- Structured JSON audit logging
- Docker multi-stage build (Alpine, non-root)
- TLS termination support
- Timezone-aware date handling

### Providers tested
- Radicale (CI-tested in integration tests)
- Nextcloud (development use)

### Known limitations
- `caldav_search_events` is client-side (server-side REPORT search planned)
- `caldav_move_event` uses copy+delete (native MOVE planned)
