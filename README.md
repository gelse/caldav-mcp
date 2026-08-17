# caldav-mcp

MCP server for **CalDAV** calendar integration. Read/write access to any
CalDAV-compatible server: Nextcloud, ownCloud, iCloud, Fastmail, etc.

Runs as a **FastMCP Streamable HTTP** server — deployable as a Docker container
on a custom port.

## Design

- **Transport**: Streamable HTTP (`/mcp` by default).
- **No authentication** on the MCP endpoint.
- **CalDAV credentials per request** via HTTP headers:
  - `X-Caldav-Url`
  - `X-Caldav-Username`
  - `X-Caldav-Password`
- Env fallback (`CALDAV_URL` / `CALDAV_USERNAME` / `CALDAV_PASSWORD`) if headers absent.

## Config

| Env | Default | Description |
|---|---|---|
| `CALDAV_MCP_PORT` | `8080` | Listen port (inside container) |
| `CALDAV_MCP_PATH` | `/mcp` | Streamable HTTP path |

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
| `caldav_list_attendees` | List attendees of an event |
| `caldav_delete_event` | Delete event by UID |
| `caldav_search_events` | Full-text search |
| `caldav_get_freebusy` | Free/busy for a time range |

## Docker Compose

```yaml
# docker-compose.yaml — maps container port 8080 -> host 8600
services:
  caldav-mcp:
    build: .
    image: caldav-mcp:latest
    restart: unless-stopped
    ports:
      - "8600:8080"
```

```bash
docker compose up -d
```

## MCP client (Streamable HTTP)

Connect your MCP client to:

```
http://<host>:8600/mcp
```

For Bifrost: add as an **HTTP** (streamable) MCP server, pointing at that URL.
Per-request CalDAV credentials are passed as headers by the client.

## Example call

```bash
curl -X POST http://localhost:8600/mcp \
  -H 'Content-Type: application/json' \
  -H 'X-Caldav-Url: https://cloud.example.com/remote.php/dav/calendars/user/' \
  -H 'X-Caldav-Username: user' \
  -H 'X-Caldav-Password: app-pass' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}'
```

## License

Apache-2.0
