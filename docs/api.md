# API Reference

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
