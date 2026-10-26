# Changelog

## v0.2.0 (2026-10-26)

### Features
- **Read-only mode (#12):** `CALDAV_MCP_READ_ONLY` flag hides write tools at registration; only the 8 read-only tools are exposed to MCP clients.
- **Pro mode / user database (#17):** `DB_CONFIG_ENABLED=true` loads config and users from a SQLite store. DB-user endpoint auth (`X-Mcp-Username` + Bearer / `X-Api-Key`), multi-remote fan-out reads with per-remote aggregation, dotted-path write addressing (`config.remote.calendar`), partial-failure reporting, multi-remote integration tests (two-Radicale suite).
- **Config store & CLI (M3):** SQLite-backed `caldav-mcp-config` CLI for managing users, configs, remotes, and calendars; Fernet encryption of CalDAV passwords at rest (`CALDAV_MCP_CONFIG_SECRET`).

### Changed
- **Breaking (M1):** CalDAV credential resolution is now mode-based — when `CALDAV_URL` is set, `X-Caldav-*` headers are ignored entirely (no per-field mixing); when unset, all three `X-Caldav-*` headers are required per request.
- **Breaking (M4):** `CALDAV_MCP_API_KEY` is ignored in pro mode; only DB-stored user credentials are accepted.

### Fixed
- DST-seasonal flakiness in `test_now_carries_server_timezone` — the test now freezes the clock to a fixed mid-DST instant, so the UTC-offset assertion is deterministic year-round (#23).
- Removed personal CalDAV values from `.env.example` (#23).

### New environment variables
- `DB_CONFIG_ENABLED` (default: `false`)
- `CALDAV_MCP_DB_PATH` (default: `""`)
- `CALDAV_MCP_CONFIG_SECRET` (default: `""`)

### Upgrade notes
- **M1:** clients relying on `X-Caldav-*` headers overriding env vars must drop `CALDAV_URL` and use header mode.
- **M4:** in pro mode use `X-Mcp-Username` + DB-stored key instead of `CALDAV_MCP_API_KEY`.
- Configuration changes require a server restart (no hot reload).

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
