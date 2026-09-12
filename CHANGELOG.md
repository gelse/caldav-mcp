# Changelog

## Unreleased

### Features
- Add `CALDAV_MCP_READ_ONLY` flag to hide write tools at registration time

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
