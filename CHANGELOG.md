# Changelog

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
