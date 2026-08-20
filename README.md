# caldav-mcp

**Give your AI assistant a calendar.** An MCP server that provides read/write
access to any CalDAV-compatible calendar — Nextcloud, Radicale, Baikal,
ownCloud, iCloud, Fastmail, and more — via 14 purpose-built tools.

## Why caldav-mcp?

| | |
|---|---|
| **Dockerized** | Multi-stage Alpine-based image, non-root execution, built-in healthcheck. Deploy anywhere Docker runs. |
| **Python 3.13** | Clean, typed codebase with Pydantic validation. Easy to read, easy to extend. |
| **Single container** | One `docker compose up` — no databases, no background workers, no sidecars. |
| **Stateless** | No session state between requests. Credentials travel per-request in HTTP headers, enabling multi-tenant use without server restarts. |
| **Secure by default** | Constant-time token comparison, per-IP rate limiting with exponential backoff, input sanitization, structured audit logging, no secrets in error responses. |
| **Two-layer auth** | Optional API key protects the MCP endpoint; CalDAV credentials are injected per-request. Independent, composable, zero surprises. |

## Where it shines

- **AI-powered calendar management** — Let Claude, Codex, or any MCP client
  create, update, search, and delete events through natural language.
- **Multi-tenant access** — Send different `X-Caldav-*` headers per request to
  access different CalDAV accounts from a single server instance.
- **Self-hosted calendar automation** — Pairs with your existing Nextcloud,
  Radicale, or Baikal server. No cloud dependency.
- **Enterprise / team deployments** — Centralized, stateless, containerized.
  Deploy behind a reverse proxy, scale horizontally, rotate credentials without
  downtime.

## Quick start

```bash
# 1. Clone the repo
git clone https://git.gelse.net/werner/caldav-mcp.git && cd caldav-mcp

# 2. Create your .env file
cp .env.example .env
# Edit .env — at minimum set CALDAV_MCP_API_KEY, CALDAV_URL, CALDAV_USERNAME, CALDAV_PASSWORD

# 3. Launch
docker compose up -d

# 4. Verify
curl -s http://localhost:8600/mcp \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "X-Caldav-Url: https://cloud.example.com/remote.php/dav/calendars/user/" \
  -H "X-Caldav-Username: user" \
  -H "X-Caldav-Password: app-password" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}'
```

The server is now reachable at `http://localhost:8600/mcp` (Streamable HTTP).

## Tools

The server exposes 14 MCP tools across three categories.

### Queries (read-only)

| Tool | Description |
|------|-------------|
| [`caldav_list_calendars`](caldav_mcp/tools/queries.py) | List all available calendars |
| [`caldav_get_events`](caldav_mcp/tools/queries.py) | Get events in a date range |
| [`caldav_get_today_events`](caldav_mcp/tools/queries.py) | Get events for today |
| [`caldav_get_week_events`](caldav_mcp/tools/queries.py) | Get events for the next 7 days |
| [`caldav_get_event_by_uid`](caldav_mcp/tools/queries.py) | Get a specific event by UID (including attendees) |
| [`caldav_search_events`](caldav_mcp/tools/queries.py) | Search events by text across summary, description, location, and categories |
| [`caldav_get_freebusy`](caldav_mcp/tools/queries.py) | Get free/busy information for a time range |

### Mutations (write)

| Tool | Description |
|------|-------------|
| [`caldav_create_event`](caldav_mcp/tools/mutations.py) | Create a new event — supports RRULE, priority, categories, attendees |
| [`caldav_update_event`](caldav_mcp/tools/mutations.py) | Partially update an existing event by UID |
| [`caldav_delete_event`](caldav_mcp/tools/mutations.py) | Delete an event by UID |
| [`caldav_move_event`](caldav_mcp/tools/mutations.py) | Move an event between calendars |

### Attendees

| Tool | Description |
|------|-------------|
| [`caldav_add_attendee`](caldav_mcp/tools/attendees.py) | Add an attendee to an event |
| [`caldav_remove_attendee`](caldav_mcp/tools/attendees.py) | Remove an attendee from an event |
| [`caldav_list_attendees`](caldav_mcp/tools/attendees.py) | List attendees of an event |

## Deployment

### Docker

The project ships with a multi-stage [`Dockerfile`](Dockerfile):

1. **Builder stage** — installs Python dependencies from
   [`requirements.txt`](requirements.txt) into a clean prefix.
2. **Runtime stage** — copies pre-built packages into a minimal Alpine image,
   runs as a non-root `app` user, exposes port `8080`.

```bash
docker build -t caldav-mcp:latest .
docker run -p 8600:8080 \
  -e CALDAV_MCP_API_KEY=YOUR_KEY \
  -e CALDAV_URL=https://cloud.example.com/remote.php/dav/calendars/user/ \
  -e CALDAV_USERNAME=user \
  -e CALDAV_PASSWORD=app-password \
  caldav-mcp:latest
```

### Docker Compose

[`docker-compose.yaml`](docker-compose.yaml) maps host port **8600** to
container port **8080** and reads environment variables from a local `.env`
file:

```yaml
services:
  caldav-mcp:
    build: .
    image: caldav-mcp:latest
    restart: unless-stopped
    ports:
      - "8600:8080"
    environment:
      CALDAV_MCP_API_KEY: "${CALDAV_MCP_API_KEY:-}"
      TZ: Europe/Vienna
```

```bash
docker compose up -d
```

A [`docker-compose.test.yaml`](docker-compose.test.yaml) is also available for
integration testing — it includes a Radicale CalDAV server.

### TLS / HTTPS

The server supports built-in TLS without a reverse proxy. Set these environment
variables to enable HTTPS directly:

```bash
CALDAV_MCP_TLS_CERT=/path/to/cert.pem
CALDAV_MCP_TLS_KEY=/path/to/key.pem
CALDAV_MCP_TLS_CA_BUNDLE=/path/to/ca.pem   # optional
```

When TLS is enabled, the server listens on HTTPS. When unset, run it behind a
reverse proxy (Traefik, Caddy, nginx) that terminates TLS.

## Authentication

Two independent layers — both optional but recommended.

### Layer 1: MCP endpoint auth

When [`CALDAV_MCP_API_KEY`](#configuration) is set, every request to the `/mcp`
endpoint must include one of:

- `Authorization: Bearer <token>`
- `X-Api-Key: <token>`

The token is compared using constant-time comparison to prevent timing
side-channel attacks. Failed attempts are tracked per client IP using a
sliding-window rate limiter with exponential backoff.

When `CALDAV_MCP_API_KEY` is unset, the endpoint is open — **do not expose it
to the public internet without authentication.**

### Layer 2: CalDAV credentials

CalDAV credentials are resolved per-request:

1. **HTTP headers** (preferred): `X-Caldav-Url`, `X-Caldav-Username`,
   `X-Caldav-Password`
2. **Environment variables** (fallback): `CALDAV_URL`, `CALDAV_USERNAME`,
   `CALDAV_PASSWORD`

HTTP headers take precedence. This enables multi-tenant usage — different
clients can target different CalDAV accounts without restarting the server.

## Configuration

All configuration is via environment variables, validated at startup with
Pydantic.

### Server

| Variable | Default | Description |
|----------|---------|-------------|
| `CALDAV_MCP_PORT` | `8080` | Listen port (inside container) |
| `CALDAV_MCP_PATH` | `/mcp` | Streamable HTTP endpoint path |
| `CALDAV_MCP_API_KEY` | `""` (disabled) | Shared secret for MCP endpoint auth |
| `TZ` | `""` (UTC) | IANA timezone (e.g. `Europe/Vienna`) for today/week boundaries |

### CalDAV

| Variable | Default | Description |
|----------|---------|-------------|
| `CALDAV_URL` | `""` | CalDAV server URL (fallback for `X-Caldav-Url` header) |
| `CALDAV_USERNAME` | `""` | CalDAV username (fallback for `X-Caldav-Username` header) |
| `CALDAV_PASSWORD` | `""` | CalDAV password (fallback for `X-Caldav-Password` header) |
| `CALDAV_MCP_CALDAV_VERIFY_SSL` | `true` | Verify TLS certs on CalDAV connections. Set `false` only for testing with self-signed certs. |

### Rate limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `CALDAV_MCP_RATE_LIMIT_MAX_FAILURES` | `10` | Max failed auth attempts per IP within the sliding window |
| `CALDAV_MCP_RATE_LIMIT_WINDOW_SECONDS` | `60` | Sliding window duration in seconds |

### TLS

| Variable | Default | Description |
|----------|---------|-------------|
| `CALDAV_MCP_TLS_CERT` | `""` | Path to TLS certificate PEM file |
| `CALDAV_MCP_TLS_KEY` | `""` | Path to TLS private key PEM file |
| `CALDAV_MCP_TLS_CA_BUNDLE` | `""` | Optional CA bundle for custom certificate authorities |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `CALDAV_MCP_LOG_FORMAT` | `text` | Audit log format: `text` or `json` |

## Security

- Put the server behind a reverse proxy that terminates **TLS**, or enable
  built-in TLS.
- Set a strong `CALDAV_MCP_API_KEY`.
- Restrict access at the network/firewall layer to trusted hosts or a VPN.
- Prefer binding to `127.0.0.1` unless you explicitly need remote access.
- Never place CalDAV app passwords in public configuration or logs.

## Development

### Commands

```bash
make test           # Run unit tests
make test-integration  # Run integration tests (requires docker-compose.test.yaml)
make test-performance  # Run performance benchmarks
make lint           # Lint with ruff (check + format)
make typecheck      # Type check with mypy
make check          # All checks: lint + typecheck + deps-check + test
make deps-check     # Verify pyproject.toml and requirements.txt are in sync
make build          # Build Docker image
```

### Project structure

```
caldav-mcp/
├── server.py                 # Thin entrypoint, launches FastMCP HTTP server
├── caldav_mcp/               # Core package
│   ├── tools/                # MCP tool handlers
│   │   ├── queries.py        #   Read-only tools (7)
│   │   ├── mutations.py      #   Write tools (4)
│   │   └── attendees.py      #   Attendee management (3)
│   ├── auth.py               # Two-layer auth (API key + CalDAV creds)
│   ├── calendar.py           # CalDAV calendar selection & serialization
│   ├── client_cache.py       # Thread-safe LRU cache for DAVClient
│   ├── config.py             # Env var parsing, header constants
│   ├── config_schema.py      # Pydantic startup validation
│   ├── datetime_utils.py     # Date/time parsing, timezone helpers
│   ├── errors.py             # Typed exceptions, ToolResult dataclass
│   ├── event_builder.py      # Pure iCalendar VEVENT construction
│   ├── sanitizers.py         # Input sanitization, field length limits
│   ├── rate_limit.py         # Sliding-window rate limiter
│   ├── audit.py              # Structured JSON audit logging
│   ├── constants.py          # Shared string constants
│   └── types.py              # CalDAVClient Protocol definition
├── tests/                    # Unit, integration, performance
├── docs/                     # Architecture, API, contributing docs
├── Dockerfile                # Multi-stage Docker build
├── docker-compose.yaml       # Production compose
├── docker-compose.test.yaml  # Test compose with Radicale
├── requirements.txt          # Runtime dependencies (pinned)
├── pyproject.toml            # Dev config and dependencies
└── Makefile                  # Build/test shortcuts
```

### Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| [`fastmcp`](https://github.com/jlowin/fastmcp) | 3.4.7 | MCP server framework, Streamable HTTP transport |
| [`caldav`](https://github.com/tobixen/python-caldav) | 3.2.1 | CalDAV client library |
| [`icalendar`](https://github.com/collective/icalendar) | 7.2.2 | iCalendar RFC 5545 parsing/generation |
| [`requests`](https://pypi.org/project/requests/) | >=2.28.0 | HTTP transport layer |

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Connection refused` | CalDAV server unreachable | Verify `CALDAV_URL` is correct and the server is running |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Self-signed or invalid TLS cert | Import the server's CA into the system trust store, or use a valid certificate |
| `ERROR:[auth] unauthorized` | Missing or invalid API token | Set `CALDAV_MCP_API_KEY` and include `Authorization: Bearer <token>` in your request |
| `Missing CalDAV credentials` | No CalDAV headers or env vars | Provide `X-Caldav-*` headers or set `CALDAV_URL`/`CALDAV_USERNAME`/`CALDAV_PASSWORD` |
| `Calendar 'X' not found` | Typo or wrong calendar name | Run `caldav_list_calendars` to see available names — they are case-sensitive |
| Events show wrong time | Server timezone not set | Set the `TZ` env var to your IANA timezone (e.g. `Europe/Vienna`) |

## FAQ

**Q: Can I use this with multiple CalDAV accounts?**
A: Yes — send different `X-Caldav-Url` / `X-Caldav-Username` / `X-Caldav-Password` headers per request. The client cache keys on `(url, username)`.

**Q: What CalDAV servers are supported?**
A: Any server implementing the CalDAV standard: Nextcloud, ownCloud, iCloud, Fastmail, Baikal, Radicale, and others.

**Q: Is the API token transmitted securely?**
A: Only when using HTTPS. Enable built-in TLS or place the server behind a TLS-terminating reverse proxy.

**Q: How do I generate a CalDAV app password?**
A: Depends on your provider. Nextcloud: Settings → Security → App Passwords. iCloud: Use an app-specific password from appleid.apple.com.

**Q: Can I use this over stdio instead of HTTP?**
A: The current server uses Streamable HTTP transport only. To use stdio, modify [`server.py`](server.py) to call `mcp.run()` instead of `mcp.run_http_async()`.

## License

[MIT](LICENSE)
