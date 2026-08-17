# caldav-mcp

MCP server for **CalDAV** calendar integration. Read/write access to any
CalDAV-compatible server: Nextcloud, ownCloud, iCloud, Fastmail, etc.

Runs as a **FastMCP STDIO** server — designed to be spawned by Bifrost (or any
MCP client). Connection settings via environment variables only.

## Tools

| Tool | Description |
|---|---|
| `caldav_list_calendars` | List all calendars |
| `caldav_get_events` | Events in a date range |
| `caldav_get_today_events` | Events today |
| `caldav_get_week_events` | Events next 7 days |
| `caldav_get_event_by_uid` | Single event by UID |
| `caldav_create_event` | Create event (summary, start, end, location, description) |
| `caldav_delete_event` | Delete event by UID |
| `caldav_search_events` | Full-text search over summary/description/location |

## Configuration

Environment variables (never hardcoded):

```
CALDAV_URL=https://cloud.example.com/remote.php/dav/calendars/user/
CALDAV_USERNAME=user
CALDAV_PASSWORD=app-specific-password
```

## Local run

```bash
pip install -r requirements.txt
CALDAV_URL=... CALDAV_USERNAME=... CALDAV_PASSWORD=... python3 server.py
```

## Docker build

```bash
docker build -t caldav-mcp .
```

## Bifrost registration (STDIO)

- **Transport**: STDIO
- **Command**: `python3`
- **Args**: `["/app/caldav-mcp/server.py"]`
- **Env**: the three `CALDAV_*` variables

## License

Apache-2.0
