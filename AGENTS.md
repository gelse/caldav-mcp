# AGENTS.md — caldav-mcp

> **TL;DR**: An MCP server that gives AI assistants read/write access to CalDAV calendars (Nextcloud, Radicale, Baikal, etc.) via 14 tools.

## Project Goal

Bridge MCP-compatible AI clients (e.g. Claude, Codex) to CalDAV servers. The server is stateless — credentials travel per-request in HTTP headers — and exposes calendar operations as MCP tools over Streamable HTTP transport.

## Architecture

| Component | Detail |
|-----------|--------|
| Language | Python 3.13, Dockerized (Alpine multi-stage) |
| MCP Framework | [FastMCP](https://github.com/jlowin/fastmcp) v3.4.7, Streamable HTTP |
| CalDAV Client | [python-caldav](https://github.com/tobixen/python-caldav) v3.2.1 |
| iCalendar | [icalendar](https://github.com/collective/icalendar) v7.2.2 (RFC 5545) |
| Entrypoint | `server.py` → FastMCP HTTP on `0.0.0.0:<port>/mcp` |

## Project Structure

```
caldav-mcp/
├── server.py                    # Thin entrypoint, launches FastMCP
├── caldav_mcp/                  # Core package
│   ├── tools/                   # MCP tool handlers
│   │   ├── queries.py           #   Read-only tools
│   │   ├── mutations.py         #   Write tools
│   │   └── attendees.py         #   Attendee management
│   ├── auth.py                  # Two-layer auth (API key + CalDAV creds)
│   ├── calendar.py              # CalDAV calendar selection & serialization
│   ├── client_cache.py          # Thread-safe LRU cache for DAVClient
│   ├── config.py                # Env var parsing, header constants
│   ├── config_schema.py         # Pydantic startup validation
│   ├── datetime_utils.py        # Date/time parsing, timezone helpers
│   ├── errors.py                # Typed exceptions, ToolResult dataclass
│   ├── event_builder.py         # Pure iCalendar VEVENT construction
│   ├── sanitizers.py            # Input sanitization, field length limits
│   ├── rate_limit.py            # Sliding-window rate limiter
│   ├── audit.py                 # Structured JSON audit logging
│   ├── constants.py             # Shared string constants
│   └── types.py                 # CalDAVClient Protocol definition
├── tests/                       # Unit, integration, performance
├── docs/                        # Architecture, API, contributing docs
├── Dockerfile                   # Multi-stage Docker build
├── docker-compose.yaml          # Production compose (port 8600→8080)
├── docker-compose.test.yaml     # Test compose with Radicale
├── requirements.txt             # Runtime dependencies
├── pyproject.toml               # Dev config and dependencies
└── Makefile                     # Build/test shortcuts
```

## MCP Tools (14 total)

### Queries (read-only)
| Tool | Description |
|------|-------------|
| `caldav_list_calendars` | List available calendars |
| `caldav_get_events` | Get events in date range |
| `caldav_get_today_events` | Events for today |
| `caldav_get_week_events` | Events for current week |
| `caldav_get_event_by_uid` | Single event by UID |
| `caldav_search_events` | Text search across events |
| `caldav_get_freebusy` | Free/busy information |

### Mutations (write)
| Tool | Description |
|------|-------------|
| `caldav_create_event` | Create new VEVENT |
| `caldav_update_event` | Update existing event |
| `caldav_delete_event` | Delete event |
| `caldav_move_event` | Move event between calendars |

### Attendees
| Tool | Description |
|------|-------------|
| `caldav_add_attendee` | Add attendee to event |
| `caldav_remove_attendee` | Remove attendee from event |
| `caldav_list_attendees` | List attendees of an event |

## Authentication Model

Two independent layers — both are optional but recommended:

1. **MCP Endpoint Auth** (`CALDAV_MCP_API_KEY` env var): Bearer token or `X-Api-Key` header. Constant-time comparison, per-IP rate limiting. Protects the MCP endpoint itself.

2. **CalDAV Credentials**: HTTP headers (`X-Caldav-Url`, `X-Caldav-Username`, `X-Caldav-Password`) take precedence over env vars (`CALDAV_URL`, `CALDAV_USERNAME`, `CALDAV_PASSWORD`). These authenticate against the actual CalDAV server.

**Key design**: Server is stateless. Only in-memory state is the LRU client cache and rate limiter.

## Configuration

All config via environment variables, validated at startup with Pydantic:

| Variable | Description |
|----------|-------------|
| `CALDAV_MCP_API_KEY` | API key for MCP endpoint auth (optional) |
| `CALDAV_URL` | CalDAV server URL (fallback, headers take precedence) |
| `CALDAV_USERNAME` | CalDAV username (fallback) |
| `CALDAV_PASSWORD` | CalDAV password (fallback) |
| `CALDAV_MCP_TLS_CERT` | TLS certificate path (optional) |
| `CALDAV_MCP_TLS_KEY` | TLS key path (optional) |
| `CALDAV_MCP_TLS_CA_BUNDLE` | Optional CA bundle for custom certificate authorities |
| `CALDAV_MCP_CALDAV_VERIFY_SSL` | Verify CalDAV server SSL certificates |
| `CALDAV_MCP_LOG_FORMAT` | Audit log format: `text` or `json` |
| `CALDAV_MCP_PORT` | HTTP server port (default `8080`) |
| `CALDAV_MCP_PATH` | Streamable HTTP endpoint path (default `/mcp`) |
| `TZ` | IANA timezone for today/week boundaries (default UTC) |
| `CALDAV_MCP_RATE_LIMIT_MAX_FAILURES` | Max failed auth attempts per IP before rate limiting (default `10`) |
| `CALDAV_MCP_RATE_LIMIT_WINDOW_SECONDS` | Sliding window for rate limiting in seconds (default `60`) |

## Deployment

- **Docker**: Multi-stage build (`python:3.13.5-alpine3.21`), non-root user, healthcheck
- **docker-compose**: Host port `8600` → container port `8080`, env vars from `.env`
- **TLS**: Optional, configured via `CALDAV_MCP_TLS_CERT` / `CALDAV_MCP_TLS_KEY`
- **Audit**: Structured JSON or text logging, controlled by `CALDAV_MCP_LOG_FORMAT`

## Git Branch Strategy

- The main branch is `main`.
- The branch `github` is deprecated and must not be used.
- The `main` branch is protected and cannot be pushed to directly.
- All development must happen in a separate branch from `main`.
- After a task is finished, a pull request must be created targeting `main`.

## Development

```bash
# Run tests
make test

# Run integration tests (requires docker-compose.test.yaml)
make test-integration

# Lint
make lint

# Type check
make typecheck
```
