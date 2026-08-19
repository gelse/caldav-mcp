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
