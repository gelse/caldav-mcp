# caldav-mcp

**Give AI assistants full read/write access to any CalDAV calendar.**

A self-hosted MCP bridge between AI assistants and CalDAV servers. Connect Claude, Cursor, VS Code, and others to Nextcloud, Radicale, Baikal, and any RFC 4791 server through a single Docker container.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker)](Dockerfile)
[![MCP](https://img.shields.io/badge/MCP-Streamable%20HTTP-8B5CF6.svg)](https://modelcontextprotocol.io)
[![Release](https://img.shields.io/github/v/release/gelse/caldav-mcp)](https://github.com/gelse/caldav-mcp/releases)
[![M8ven Score](https://m8ven.ai/badge/mcp/gelse-caldav-mcp-p1nzjs?v=90357b3ccae3ec55ec82a3b6459ff45c)](https://m8ven.ai/mcp/gelse-caldav-mcp-p1nzjs)

## What problem does it solve

Bridges any MCP-compatible AI client (Claude, Cursor, VS Code, Codex, …) to
any RFC 4791 CalDAV server (Nextcloud, Radicale, Baikal, …) so the assistant
can read and write your calendar directly.

Ask your assistant:

- **"What's on my calendar tomorrow?"** — [`caldav_get_today_events`](caldav_mcp/tools/queries.py)
- **"Create a meeting next Tuesday at 14:00."** — [`caldav_create_event`](caldav_mcp/tools/mutations.py)
- **"Move this event to my personal calendar."** — [`caldav_move_event`](caldav_mcp/tools/mutations.py)
- **"When am I free next week?"** — [`caldav_get_freebusy`](caldav_mcp/tools/queries.py)

## What it does NOT do

- **No stdio transport** — Streamable HTTP only. To use stdio, modify [`server.py`](server.py) to call `mcp.run()` instead of `mcp.run_http_async()`.
- **Not a CalDAV server** — you need an existing CalDAV server (Nextcloud, Radicale, Baikal, etc.).
- **No iTIP/imip scheduling** — attendees are stored on events, but no email invitations or scheduling messages are sent.
- **Client-side search** — [`caldav_search_events`](caldav_mcp/tools/queries.py) fetches all events and filters locally. Works well for small-to-medium calendars; may be slow on very large ones.
- **Non-atomic move** — [`caldav_move_event`](caldav_mcp/tools/mutations.py) copies the event then deletes the original. A failure after copy leaves a duplicate (the safer failure mode).
- **No hot-reload** — config changes require a server restart. The store is frozen into an immutable singleton at startup.
- **Self-hosted only** — not a SaaS. You run and operate the server yourself.

## Quick start

### docker-compose

```yaml
services:
  caldav-mcp:
    image: ghcr.io/gelse/caldav-mcp:latest
    ports:
      - "8600:8080"
    environment:
      CALDAV_URL: https://cloud.example.com/remote.php/dav/calendars/user/
      CALDAV_USERNAME: user
      CALDAV_PASSWORD: app-password
      CALDAV_MCP_API_KEY: your-secret-token
      TZ: Europe/Vienna
```

```bash
docker compose up -d
```

> **Note:** `CALDAV_URL`, `CALDAV_USERNAME`, and `CALDAV_PASSWORD` are
> optional. When set, `X-Caldav-*` request headers are ignored (environment
> mode). When omitted, send `X-Caldav-Url`, `X-Caldav-Username`, and
> `X-Caldav-Password` headers per request (header mode).

### Verify

```bash
curl -s http://localhost:8600/mcp \
  -H "Authorization: Bearer your-secret-token" \
  -H "X-Caldav-Url: https://cloud.example.com/remote.php/dav/calendars/user/" \
  -H "X-Caldav-Username: user" \
  -H "X-Caldav-Password: app-password" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}'
```

### MCP client config

```json
{
  "mcpServers": {
    "caldav": {
      "type": "http",
      "url": "http://localhost:8600/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY",
        "X-Caldav-Url": "https://cloud.example.com/remote.php/dav/calendars/user/",
        "X-Caldav-Username": "user",
        "X-Caldav-Password": "app-password"
      }
    }
  }
}
```

Client-specific config files (Claude Desktop, Claude Code, Cursor, VS Code, OpenCode, OpenWebUI): see [`docs/clients.md`](docs/clients.md).

## Why this over other MCP calendar servers

- **Self-hosted** — no data leaves your network. No third-party SaaS.
- **Stateless single container** — no database, no sidecars. Optional SQLite store for pro mode.
- **Any RFC 4791 server** — Radicale CI-integration-tested, Nextcloud used in development. Baikal, ownCloud, iCloud, Fastmail are protocol-compatible.
- **Multi-account without restarts** — credentials travel per-request in HTTP headers.
- **Pro mode** — optional SQLite store with per-user keys, fan-out reads across multiple remotes. See [`docs/pro-mode.md`](docs/pro-mode.md).
- **Security built in** — optional API key auth, per-IP rate limiting, structured audit logging, read-only mode.

## Why Docker (not npx)

caldav-mcp is Python-based (not Node), so npx is not an option. Docker means:

- No local Python, venv, or dependency management — the pre-built image has everything pinned.
- Multi-arch support — runs anywhere Docker runs.
- One container shared by your entire homelab or team, serving multiple AI clients.

## The 14 tools

**Queries (7)** — read-only

| Tool | Description |
|------|-------------|
| [`caldav_list_calendars`](caldav_mcp/tools/queries.py) | List all available calendars |
| [`caldav_get_events`](caldav_mcp/tools/queries.py) | Get events in a date range |
| [`caldav_get_today_events`](caldav_mcp/tools/queries.py) | Get events for today |
| [`caldav_get_week_events`](caldav_mcp/tools/queries.py) | Get events for the next 7 days |
| [`caldav_get_event_by_uid`](caldav_mcp/tools/queries.py) | Get a specific event by UID |
| [`caldav_search_events`](caldav_mcp/tools/queries.py) | Find events by text |
| [`caldav_get_freebusy`](caldav_mcp/tools/queries.py) | Get free/busy information |

**Mutations (4)** — write

| Tool | Description |
|------|-------------|
| [`caldav_create_event`](caldav_mcp/tools/mutations.py) | Create a new event |
| [`caldav_update_event`](caldav_mcp/tools/mutations.py) | Partially update an existing event |
| [`caldav_delete_event`](caldav_mcp/tools/mutations.py) | Delete an event |
| [`caldav_move_event`](caldav_mcp/tools/mutations.py) | Move an event between calendars |

**Attendees (3)**

| Tool | Description |
|------|-------------|
| [`caldav_add_attendee`](caldav_mcp/tools/attendees.py) | Add an attendee to an event |
| [`caldav_remove_attendee`](caldav_mcp/tools/attendees.py) | Remove an attendee from an event |
| [`caldav_list_attendees`](caldav_mcp/tools/attendees.py) | List attendees of an event |

Full API docs: [`docs/api.md`](docs/api.md).

## Authentication & security

- **API key auth** — optional `CALDAV_MCP_API_KEY` with constant-time comparison. Pro mode uses PBKDF2-HMAC-SHA256 DB-stored keys.
- **Rate limiting** — per-IP sliding window (default: 10 failures / 60 s), configurable via `CALDAV_MCP_RATE_LIMIT_MAX_FAILURES` and `CALDAV_MCP_RATE_LIMIT_WINDOW_SECONDS`.
- **Audit logging** — all auth attempts and tool operations logged. Set `CALDAV_MCP_LOG_FORMAT=json` for structured output.
- **Read-only mode** — `CALDAV_MCP_READ_ONLY=true` hides all write tools, leaving only the 8 read-only tools visible.

## Configuration essentials

Most users only need these:

| Variable | Description |
|----------|-------------|
| `CALDAV_URL` | CalDAV server URL (set = env mode; unset = header mode) |
| `CALDAV_USERNAME` | CalDAV username (env mode) |
| `CALDAV_PASSWORD` | CalDAV password (env mode) |
| `CALDAV_MCP_API_KEY` | API key for endpoint auth (optional) |
| `TZ` | IANA timezone for today/week boundaries (default: UTC) |
| `CALDAV_MCP_READ_ONLY` | `true` to hide write tools |

**Credential modes:** environment variables (env mode), per-request `X-Caldav-*` headers (header mode), or SQLite store (pro mode). No per-field mixing — all three credentials come from the same source. Full reference: [`docs/configuration.md`](docs/configuration.md).

**Pro mode** (`DB_CONFIG_ENABLED=true`): loads users and configs from a SQLite store at startup. Supports per-user keys, fan-out reads across multiple remotes, and dotted-path writes. Requires `CALDAV_MCP_DB_PATH` and `CALDAV_MCP_CONFIG_SECRET`. Config changes require a restart. Details: [`docs/pro-mode.md`](docs/pro-mode.md), [`docs/cli.md`](docs/cli.md).

## Documentation

| Document | Contents |
|----------|----------|
| [`docs/api.md`](docs/api.md) | API reference — auth headers, addressing, aggregated results |
| [`docs/clients.md`](docs/clients.md) | MCP client configuration (Claude Desktop, Cursor, VS Code, …) |
| [`docs/cli.md`](docs/cli.md) | Config store CLI (`caldav-mcp-config`) |
| [`docs/configuration.md`](docs/configuration.md) | Full env var reference, TLS, reverse proxy, deployment |
| [`docs/pro-mode.md`](docs/pro-mode.md) | Pro mode — SQLite store, fan-out, per-user auth |
| [`docs/architecture.md`](docs/architecture.md) | Architecture and design decisions |
| [`docs/contributing.md`](docs/contributing.md) | Development setup, code style, architecture rules |

## Known gaps & planned features

- Client-side search does not scale to very large calendars.
- `caldav_move_event` is non-atomic (copy + delete; duplicate on failure).
- Config changes require a server restart — no hot-reload yet.
- No stdio transport — Streamable HTTP only.
- Version `0.1.0` — pre-1.0, API may change between releases.
- `X-Caldav-Username` / `X-Caldav-Password` passthrough mode reserved for future use.
- Future: hot-reload, pro-mode enhancements (see [`ideas/`](ideas/)).

## FAQ

**I get `ERROR:[auth] unauthorized`** — Set `CALDAV_MCP_API_KEY` and include
`Authorization: Bearer <token>` (or `X-Api-Key`) in every request. When
`CALDAV_MCP_API_KEY` is unset the endpoint is open, but never expose it
to the internet without auth.

**Calendar not found** — Calendar names are **case-sensitive**. Run
`caldav_list_calendars` to see the exact names your server reports.

**Events show wrong time** — Set the `TZ` environment variable to your IANA
timezone (e.g. `Europe/Vienna`). Without it, today/week boundaries default
to UTC.

**SSL certificate errors** — For self-signed certs, either import the CA into
the system trust store or set `CALDAV_MCP_CALDAV_VERIFY_SSL=false` for
testing.

**Can I use stdio?** — Not out of the box. The server uses Streamable HTTP
transport. To add stdio support, modify [`server.py`](server.py) to call
`mcp.run()` instead of `mcp.run_http_async()`.

## Contributing

See [`docs/contributing.md`](docs/contributing.md).

## License

[MIT](LICENSE)
