# caldav-mcp

MCP server for **CalDAV** calendar integration. Read/write access to any
CalDAV-compatible server: Nextcloud, ownCloud, iCloud, Fastmail, etc.

Runs as a **FastMCP Streamable HTTP** server — deployable as a Docker container
on a custom port.

## Design

- **Transport**: Streamable HTTP (`/mcp` by default).
- **Token authentication** on the MCP endpoint: requests must present a valid
  `Authorization: Bearer <token>` or `X-Api-Key: <token>` header matching the
  `CALDAV_MCP_API_KEY` environment variable. Auth is disabled when that variable
  is unset.
- **CalDAV credentials per request** via HTTP headers:
  - `X-Caldav-Url`
  - `X-Caldav-Username`
  - `X-Caldav-Password`
- Env fallback (`CALDAV_URL` / `CALDAV_USERNAME` / `CALDAV_PASSWORD`) if headers absent.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                 server.py                        │
│            (thin entrypoint)                     │
└──────────────────────┬──────────────────────────┘
                       │ imports
┌──────────────────────▼──────────────────────────┐
│              caldav_mcp/ package                 │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐ │
│  │ config   │  │ errors   │  │ client_cache  │ │
│  │ (env,    │  │ (typed   │  │ (LRU cache    │ │
│  │  TZ)     │  │  results)│  │  for DAVClient│ │
│  └──────────┘  └──────────┘  └───────────────┘ │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐ │
│  │ auth     │  │ datetime │  │ calendar      │ │
│  │ (guards) │  │ _utils   │  │ (event I/O)   │ │
│  └──────────┘  └──────────┘  └───────────────┘ │
│                                                  │
│  ┌─────────────────────────────────────────────┐│
│  │              tools/                          ││
│  │  @mcp.tool() handlers + with_caldav_client  ││
│  └─────────────────────────────────────────────┘│
└─────────────────────────────────────────────────┘
```

See [`docs/architecture.md`](./docs/architecture.md) for the full design document.

## Security

The MCP endpoint **has no built-in authentication by default** and grants
read/write access to any CalDAV calendar you configure. It may be left fully
unauthenticated when `CALDAV_MCP_API_KEY` is unset. **Do not expose it directly
to the public internet.**

- Put the server behind a reverse proxy (e.g. Traefik, Caddy, nginx) that terminates **TLS**.
- Restrict access at the network/firewall layer to trusted hosts or a private VPN.
- Prefer binding the container port to `127.0.0.1` unless you explicitly need remote access.
- Never place CalDAV app passwords or the endpoint in public configuration or logs.

## Config
| Env | Default | Description |
|---|---|---|
| `CALDAV_URL` | *(none)* | CalDAV server URL. Env fallback for the `X-Caldav-Url` request header. |
| `CALDAV_USERNAME` | *(none)* | CalDAV username. Env fallback for the `X-Caldav-Username` request header. |
| `CALDAV_PASSWORD` | *(none)* | CalDAV password. Env fallback for the `X-Caldav-Password` request header. |
| `CALDAV_MCP_PORT` | `8080` | Listen port (inside container). **Startup only** — changing at runtime has no effect. |
| `CALDAV_MCP_PATH` | `/mcp` | Streamable HTTP path. **Startup only** — changing at runtime has no effect. |
| `CALDAV_MCP_API_KEY` | *(none)* | Shared secret API token. When set, requests must include a matching `Authorization: Bearer <token>` or `X-Api-Key: <token>` header. |
| `TZ` | `UTC` | Server timezone (e.g. `Europe/Vienna`) used for "today"/"week" boundaries and date-only inputs. Reads the `TZ` env var via `zoneinfo`; falls back to `UTC` when unset, empty, or invalid. |

## Tools

| Tool | Description |
|---|---|
| `caldav_list_calendars` | List all calendars |
| `caldav_get_events` | Events in a date range |
| `caldav_get_today_events` | Events today |
| `caldav_get_week_events` | Events next 7 days |
| `caldav_get_event_by_uid` | Single event by UID (incl. attendees) |
| `caldav_create_event` | Create event (summary, start, end, location, description, categories, priority, rrule, attendees) |
| `caldav_update_event` | Update event by UID (summary, start, end, location, description) |
| `caldav_add_attendee` | Add attendee to an event |
| `caldav_remove_attendee` | Remove attendee from an event |
| `caldav_list_attendees` | List attendees of an event |
| `caldav_move_event` | Move event to another calendar |
| `caldav_delete_event` | Delete event by UID |
| `caldav_search_events` | Full-text search |
| `caldav_get_freebusy` | Free/busy for a time range |

## Development

- **Checks**: linting, type checking and tests are enforced via the Makefile and run
  automatically in CI:

  ```bash
  make lint  # runs ruff linter and format check
  make typecheck  # runs mypy type checker
  make check  # runs all checks (lint + typecheck + tests)
  ```

- **Dependencies**: installed from [`requirements.txt`](./requirements.txt) (or via the
  `dependencies` list in [`pyproject.toml`](./pyproject.toml)). Includes `icalendar`, which is used
  to build and correctly RFC 5545-escape event payloads.
  Dependency versions are **pinned** in both files for reproducible builds;
  `make deps-check` verifies they stay in sync.
- **Tests**: unit tests live in [`tests/`](./tests) and run with `pytest` via
  the Makefile:

  ```bash
  make test
  ```

  This uses the project virtual environment at `./.venv`
  (`./.venv/bin/pytest`). The standard library `unittest` runner also works
  but is not used in CI:

  ```bash
  python -m unittest discover -s tests -v
  ```

  The suite covers escaping of special characters (`\`, `,`, `;`, newlines),
  attendees, priority/rrule validation, and edge cases (emoji, empty optional
  fields) for `caldav_create_event`.

## Docker Compose

```yaml
services:
  caldav-mcp:
    build: .
    image: caldav-mcp:latest
    restart: unless-stopped
    ports:
      - "8600:8080"
    environment:
      # Optional; set the API key in a local .env file (see below).
      CALDAV_MCP_API_KEY: "${CALDAV_MCP_API_KEY:-}"
```

```bash
docker compose up -d
```

To set the API key, create a `.env` file next to the compose file:

```bash
CALDAV_MCP_API_KEY=CHANGE_ME
```

`docker compose` loads `.env` automatically and injects the value into the container. When
unset, the token is empty and authentication is disabled.

## MCP client (Streamable HTTP)

Connect your MCP client to `http://<host>:8600/mcp`.

## Security note

The server binds `0.0.0.0` and is published via the Docker Compose port mapping.
When exposing it beyond `localhost`, place it behind a reverse proxy that
terminates TLS (HTTPS) so the API token is not transmitted in cleartext. Always set
a strong `CALDAV_MCP_API_KEY`.

## Troubleshooting

### Connection Errors

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Connection refused` | CalDAV server unreachable | Verify `CALDAV_URL` is correct and the server is running. Check firewall rules. |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Self-signed or invalid TLS cert | The `caldav` library uses system CA certs. Import your server's CA into the system trust store, or use a valid certificate. |
| `Timeout` | Network latency or server overload | Increase the timeout on your CalDAV server, or check network connectivity. |

### Authentication Failures

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ERROR:[auth] unauthorized` | Missing or invalid API token | Set `CALDAV_MCP_API_KEY` and send it as `Authorization: Bearer <token>` or `X-Api-Key: <token>`. |
| `Missing CalDAV credentials` | No CalDAV headers or env-vars | Provide `X-Caldav-Url`, `X-Caldav-Username`, `X-Caldav-Password` headers, or set `CALDAV_URL`, `CALDAV_USERNAME`, `CALDAV_PASSWORD` env-vars. |
| `401 Unauthorized` from CalDAV server | Wrong CalDAV username/password | Verify your CalDAV app password is correct. Some providers require app-specific passwords. |

### Calendar Not Found

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Calendar 'X' not found` | Typo or wrong calendar name | Run `caldav_list_calendars` to see available names. Names are case-sensitive. |
| `No calendars found for this principal` | CalDAV URL points to wrong path | Ensure `CALDAV_URL` ends with the correct calendar root (e.g. `/remote.php/dav/calendars/user/`). |

### Timezone Problems

| Symptom | Cause | Fix |
|---------|-------|-----|
| Events show wrong time | Server timezone not configured | Set the `TZ` env-var to your IANA timezone (e.g. `Europe/Vienna`). Defaults to UTC. |
| Date-only inputs return wrong range | Date interpreted in UTC | Set `TZ` to your local timezone so "today" boundaries match your expectations. |

## FAQ

**Q: Can I use this with multiple CalDAV accounts?**
A: Yes — send different `X-Caldav-Url`/`X-Caldav-Username`/`X-Caldav-Password` headers per request. The client cache keys on `(url, username)`.

**Q: Is the API token transmitted securely?**
A: Only if you use HTTPS. Always place the server behind a TLS-terminating reverse proxy.

**Q: What CalDAV servers are supported?**
A: Any server implementing the CalDAV standard: Nextcloud, ownCloud, iCloud, Fastmail, Baikal, Radicale, etc.

**Q: How do I generate a CalDAV app password?**
A: This depends on your provider. Nextcloud: Settings → Security → App Passwords. iCloud: Use an app-specific password from appleid.apple.com.

**Q: Can I use the MCP tools over stdio instead of HTTP?**
A: The current server uses Streamable HTTP transport only. To use stdio, you would need to modify `server.py` to call `mcp.run()` instead of `mcp.run_http_async()`.

## Example call

```bash
curl -X POST http://localhost:8600/mcp \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer CHANGE_ME' \
  -H 'X-Caldav-Url: https://cloud.example.com/remote.php/dav/calendars/user/' \
  -H 'X-Caldav-Username: user' \
  -H 'X-Caldav-Password: app-pass' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}'
```

Replace `CHANGE_ME` with the value of `CALDAV_MCP_API_KEY`. You may use
`-H 'X-Api-Key: CHANGE_ME'` as an alternative.

## License

Apache-2.0
