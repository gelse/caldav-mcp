# API Reference

## Authentication headers

All requests to `/mcp` require authentication when `CALDAV_MCP_API_KEY` is set (simple mode) or when `DB_CONFIG_ENABLED=true` (pro mode).

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization: Bearer <token>` | Yes | API key (simple mode) or DB user key (pro mode) |
| `X-Api-Key: <token>` | Alternative | Same as Bearer; either header is accepted |
| `X-Mcp-Username` | Pro mode only | DB username; required when `DB_CONFIG_ENABLED=true` |
| `X-Caldav-Url` | Header mode / passthrough | CalDAV server URL (required in header mode; used for passthrough remotes in pro mode; ignored in env mode) |
| `X-Caldav-Username` | Header mode / passthrough | CalDAV username (same semantics as `X-Caldav-Url`) |
| `X-Caldav-Password` | Header mode / passthrough | CalDAV password (same semantics as `X-Caldav-Url`) |

**Header semantics by mode:**

| Mode | `X-Caldav-*` behavior |
|------|----------------------|
| Env (`CALDAV_URL` set) | Ignored entirely |
| Header (`CALDAV_URL` unset) | Required on every request — all three headers |
| Pro (`DB_CONFIG_ENABLED=true`) | Used only for passthrough remotes; ignored for direct remotes |

`X-Caldav-Username` and `X-Caldav-Password` are reserved for a future passthrough mode and are ignored when `CALDAV_URL` is set.

## Addressing model

**Simple mode** (env / header): tools accept a plain `calendar_name` string (e.g. `"work"`). The calendar is looked up from the configured remote.

**Pro mode** (`DB_CONFIG_ENABLED=true`): write tools require a dotted-path identifier in the form `config.remote.calendar` (e.g. `"main.radicale.work"`). Plain names are rejected with an error. For parameterless read tools (e.g. `caldav_list_calendars`), an empty `calendar_name` fans out across all accessible remotes and calendars. A dotted path narrows the query to a single calendar.

Dotted paths have exactly three dot-separated segments. Config, remote, and calendar names must not contain dots (enforced by the store's charset regex). See [`docs/cli.md`](cli.md) for managing configs, remotes, and calendars.

## Aggregated read results (pro mode)

Read tools fan out across every accessible remote and return one entry per `(config, remote, calendar)` pair. Each entry is addressed by the dotted path components (`config_name`, `remote_name`, `calendar_name`) and carries either the tool's own payload under `data` or, on failure, an `error` string. The per-entry status vocabulary below is what the rendered `message` reports for each remote — it is not a separate JSON field on each entry.

### Per-entry status vocabulary

| Status | Meaning |
|--------|---------|
| `ok` | Success — data returned |
| `empty` | Success — no matching data (zero results) |
| `auth` | `AuthError` — missing or invalid CalDAV credentials |
| `not_found` | `NotFoundError` — calendar or event does not exist |
| `error` | Any other exception |

### Top-level status

- `status`: `ok` when at least one entry succeeded, `error` when all failed, `empty` for zero accessible calendars.
- `message`: human-readable per-remote summary (see rendered format below).

### Example: `caldav_get_events` with partial failure

```json
{
  "status": "ok",
  "message": "OK Fan-out across 3 scopes: 2 ok, 1 error\n- [ok] work.nextcloud: 2 calendars\n- [error] work.radicale: connection refused",
  "data": [
    {
      "config_name": "work",
      "remote_name": "nextcloud",
      "calendar_name": "meetings",
      "data": [
        {"uid": "evt-1@nextcloud", "summary": "Sprint planning", "dtstart": "2026-09-15T10:00:00", "dtend": "2026-09-15T11:00:00"}
      ]
    },
    {
      "config_name": "work",
      "remote_name": "nextcloud",
      "calendar_name": "personal",
      "data": [
        {"uid": "evt-2@nextcloud", "summary": "Dentist", "dtstart": "2026-09-16T14:00:00", "dtend": "2026-09-16T15:00:00"}
      ]
    },
    {
      "config_name": "work",
      "remote_name": "radicale",
      "calendar_name": "personal",
      "error": "connection refused"
    }
  ]
}
```

### Rendered message format

The `message` field is produced by `render_fanout_message` and always follows this structure:

1. **First line**: top-level tag (`OK` / `ERROR:[server]`) + scope count + ok/error counts
2. **Detail lines**: one `- [status] config.remote: detail` line per remote in declaration order

When a remote spans several calendars, entries are grouped into a single line with a count (e.g. `2 calendars`). On failure, the detail is the exception text. Mixed outcomes within one remote use severity-based status (highest: `error` > `auth` > `not_found`).

```
OK Fan-out across 3 scopes: 2 ok, 1 error
- [ok] work.nextcloud: 2 calendars
- [error] work.radicale: connection refused
```

For all-failed results, the first line uses the `ERROR:[server]` tag:

```
ERROR:[server] Fan-out across 2 scopes: 2 error
- [auth] work.nextcloud: unauthorized - missing credentials
- [error] work.radicale: connection refused
```

## MCP Tools

All tools are accessible via the Streamable HTTP endpoint at `/mcp`.

> **Read-only mode:** When `CALDAV_MCP_READ_ONLY=true`, only 8 read-only
> tools (the 7 query tools plus `caldav_list_attendees`) below are registered.  Write tools (`caldav_create_event`,
> `caldav_update_event`, `caldav_delete_event`, `caldav_move_event`,
> `caldav_add_attendee`, `caldav_remove_attendee`) are hidden and cannot be
> called via MCP.

### caldav_list_calendars

List all calendars available for the configured account.

**Parameters:** None

**Returns:** `ToolResult` with `data` = list of `{name, url}` dicts.

### caldav_get_events

Get events in a date range for a calendar.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `calendar_name` | str | `""` | Calendar name (defaults to first) |
| `start` | str | `""` | Start datetime (ISO 8601; defaults to today 00:00) |
| `end` | str | `""` | End datetime (defaults to start + 1 day) |

### caldav_get_today_events

Get events for today (00:00 to 24:00).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `calendar_name` | str | `""` | Calendar name |

### caldav_get_week_events

Get events for the next 7 days.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `calendar_name` | str | `""` | Calendar name |

### caldav_get_event_by_uid

Get a specific event by its UID (includes attendees).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | str | *(required)* | Event UID |
| `calendar_name` | str | `""` | Calendar name |

### caldav_create_event

> **Not available in read-only mode.**

Create a new calendar event.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `summary` | str | *(required)* | Event title |
| `start` | str | *(required)* | Start datetime (ISO 8601) |
| `end` | str | `""` | End datetime (defaults to start + 1 hour) |
| `calendar_name` | str | `""` | Calendar name |
| `location` | str | `""` | Event location |
| `description` | str | `""` | Event description |
| `categories` | str | `""` | Comma-separated categories |
| `priority` | str | `""` | Priority 0-9 |
| `rrule` | str | `""` | RRULE string (RFC 5545) |
| `attendees` | str | `""` | Comma-separated email addresses |

### caldav_update_event

> **Not available in read-only mode.**

Update an existing event by UID. Only provided fields are updated.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | str | *(required)* | Event UID |
| `summary` | str | `""` | New summary |
| `start` | str | `""` | New start datetime |
| `end` | str | `""` | New end datetime |
| `calendar_name` | str | `""` | Calendar name |
| `location` | str | `""` | New location |
| `description` | str | `""` | New description |

### caldav_add_attendee

> **Not available in read-only mode.**

Add an attendee to an existing event.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | str | *(required)* | Event UID |
| `email` | str | *(required)* | Attendee email |
| `calendar_name` | str | `""` | Calendar name |
| `role` | str | `"REQ-PARTICIPANT"` | RFC 5545 ROLE |

### caldav_remove_attendee

> **Not available in read-only mode.**

Remove an attendee from an existing event.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | str | *(required)* | Event UID |
| `email` | str | *(required)* | Attendee email |
| `calendar_name` | str | `""` | Calendar name |

### caldav_list_attendees

List attendees of an event.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | str | *(required)* | Event UID |
| `calendar_name` | str | `""` | Calendar name |

### caldav_move_event

> **Not available in read-only mode.**

Move an event to another calendar (copy with new UID, delete original).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | str | *(required)* | Event UID |
| `target_calendar` | str | *(required)* | Destination calendar name |
| `source_calendar` | str | `""` | Source calendar (defaults to first) |

### caldav_delete_event

> **Not available in read-only mode.**

Delete an event by UID.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | str | *(required)* | Event UID |
| `calendar_name` | str | `""` | Calendar name |

### caldav_search_events

Search events by text (summary/description/location).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | str | *(required)* | Search text |
| `calendar_name` | str | `""` | Calendar name |

### caldav_get_freebusy

Get free/busy information for a time range.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `start` | str | `""` | Start datetime (defaults to today 00:00) |
| `end` | str | `""` | End datetime (defaults to start + 1 day) |
| `calendar_name` | str | `""` | Calendar name |

## ToolResult

Every tool returns a `ToolResult` dataclass:

| Field | Type | Description |
|-------|------|-------------|
| `status` | `Status` | `ok`, `empty`, `auth`, `not_found`, or `error` |
| `message` | `str` | Human-readable text |
| `data` | `Any` | Optional structured payload |
