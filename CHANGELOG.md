# Changelog

## Unreleased

### Features
- Add `CALDAV_MCP_READ_ONLY` flag to hide write tools at registration time
- Read-only config singleton (`caldav_mcp.app_config`) as the single source of CalDAV connection data; simple mode backed by an env-derived built-in config
- **Pro mode (M4):** `DB_CONFIG_ENABLED=true` loads config and users from the SQLite store at startup. DB-user auth (`X-Mcp-Username` + Bearer/`X-Api-Key`), fan-out reads across remotes, dotted-path write addressing (`config.remote.calendar`).

### Changed
- **Breaking:** CalDAV credential resolution is now mode-based. If `CALDAV_URL` is set, all credentials come from the environment and `X-Caldav-Url`/`X-Caldav-Username`/`X-Caldav-Password` request headers are ignored (previously headers took precedence per-field). If `CALDAV_URL` is unset, the three `X-Caldav-*` headers are required per request. No per-field mixing. `X-Caldav-Username`/`X-Caldav-Password` are reserved for a future passthrough mode.
- CalDAV credentials are resolved via the config singleton; behavior is unchanged from the mode-based rule described above
- **Behavioral:** `CALDAV_MCP_API_KEY` is ignored in pro mode (`DB_CONFIG_ENABLED=true`). Only DB-stored user credentials are accepted.

### Note
- Config changes take effect on restart

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
