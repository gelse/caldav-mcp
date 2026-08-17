#!/usr/bin/env python3
"""
CalDAV MCP Server (Streamable HTTP)

Exposes read/write access to any CalDAV-compatible calendar server
(Nextcloud, ownCloud, iCloud, Fastmail, etc.) via the Model Context Protocol
over the Streamable HTTP transport.

Design:
- Runs as a Docker container, listening on a configurable port (default 8080).
- No authentication on the MCP endpoint itself.
- CalDAV credentials are supplied PER REQUEST as HTTP headers:

    X-Caldav-Url        e.g. https://cloud.example.com/remote.php/dav/calendars/user/
    X-Caldav-Username   CalDAV username
    X-Caldav-Password   CalDAV password (or app-specific password)

  Fallback to environment variables CALDAV_URL / CALDAV_USERNAME /
  CALDAV_PASSWORD if the headers are absent (convenient for local use).
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from caldav import DAVClient
from fastmcp import FastMCP, Context

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_PORT = int(os.environ.get("CALDAV_MCP_PORT", "8080"))
DEFAULT_PATH = os.environ.get("CALDAV_MCP_PATH", "/mcp")

# Header names (lowercase for lookup; FastMCP/ASGI normalizes to lowercase)
HDR_URL = "x-caldav-url"
HDR_USERNAME = "x-caldav-username"
HDR_PASSWORD = "x-caldav-password"


mcp = FastMCP(
    "caldav-mcp",
    instructions=(
        "CalDAV calendar access (read + write). Use caldav_list_calendars first, "
        "then operate on events by UID. Times are ISO 8601. CalDAV credentials "
        "are read from the X-Caldav-Url / X-Caldav-Username / X-Caldav-Password "
        "request headers."
    ),
)


# ---------------------------------------------------------------------------
# Credential resolution (per-request headers, env fallback)
# ---------------------------------------------------------------------------


class CalDAVError(Exception):
    pass


def _resolve_credentials(ctx: Context | None) -> tuple[str, str, str]:
    """Return (url, username, password) from request headers, falling back to env."""
    headers = _request_headers(ctx)

    url = headers.get(HDR_URL) or os.environ.get("CALDAV_URL", "")
    username = headers.get(HDR_USERNAME) or os.environ.get("CALDAV_USERNAME", "")
    password = headers.get(HDR_PASSWORD) or os.environ.get("CALDAV_PASSWORD", "")

    if not url or not username or not password:
        raise CalDAVError(
            "Missing CalDAV credentials. Provide X-Caldav-Url, X-Caldav-Username, "
            "X-Caldav-Password headers, or set CALDAV_URL/CALDAV_USERNAME/"
            "CALDAV_PASSWORD environment variables."
        )
    return url, username, password


def _request_headers(ctx: Context | None) -> dict:
    """Extract HTTP request headers from the FastMCP context (best effort)."""
    if ctx is None:
        return {}
    # FastMCP Context exposes the underlying request via .request_context / .meta
    # depending on version. Try several accessors defensively.
    for attr in ("request_context", "request", "meta"):
        obj = getattr(ctx, attr, None)
        if obj is None:
            continue
        headers = getattr(obj, "headers", None)
        if isinstance(headers, dict):
            return {k.lower(): v for k, v in headers.items()}
        # Some versions expose a Headers object (multidict-like)
        if hasattr(headers, "get"):
            try:
                return {k.lower(): headers.get(k) for k in headers.items()}
            except Exception:
                pass
    return {}


# ---------------------------------------------------------------------------
# CalDAV helpers
# ---------------------------------------------------------------------------


def _client(url: str, username: str, password: str) -> DAVClient:
    return DAVClient(url=url, username=username, password=password)


def _get_calendar(client: DAVClient, calendar_name: str = ""):
    """Resolve a calendar by name, or return the default (first) calendar."""
    calendars = client.principal().calendars()
    if not calendars:
        raise ValueError("No calendars found for this principal")
    if calendar_name:
        for c in calendars:
            if c.name == calendar_name:
                return c
        raise ValueError(
            f"Calendar '{calendar_name}' not found. Available: "
            + ", ".join(c.name for c in calendars)
        )
    return calendars[0]


def _parse_dt(value: str) -> datetime:
    value = value.strip()
    if not value:
        return datetime.now(timezone.utc)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M%z",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Could not parse datetime: {value!r}")


def _format_ical_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _event_to_dict(event) -> dict:
    ical = event.icalendar_instance
    vevent = ical.vobject_instance.vevent
    summary = getattr(vevent, "summary", None)
    dtstart = getattr(vevent, "dtstart", None)
    dtend = getattr(vevent, "dtend", None)
    uid_attr = getattr(vevent, "uid", None)
    categories = getattr(vevent, "categories", None)
    return {
        "uid": str(uid_attr.value) if uid_attr is not None else event.id,
        "summary": str(summary.value) if summary is not None else "",
        "dtstart": str(dtstart.value) if dtstart is not None else "",
        "dtend": str(dtend.value) if dtend is not None else "",
        "location": str(vevent.location.value) if getattr(vevent, "location", None) else "",
        "description": str(vevent.description.value) if getattr(vevent, "description", None) else "",
        "categories": (
            ",".join(str(x) for x in categories.value_list)
            if categories is not None
            else ""
        ),
    }


def _context_arg(ctx: Context | None) -> Context:
    # FastMCP injects Context if a parameter is annotated Context and named ctx.
    return ctx


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def caldav_list_calendars(ctx: Context = None) -> str:
    """List all calendars available for the configured account."""
    try:
        url, user, pw = _resolve_credentials(ctx)
        calendars = _client(url, user, pw).principal().calendars()
        if not calendars:
            return "No calendars found"
        return "\n".join(f"- {c.name} (url: {c.url})" for c in calendars)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def caldav_get_events(
    calendar_name: str = "",
    start: str = "",
    end: str = "",
    ctx: Context = None,
) -> str:
    """
    Get events in a date range for a calendar.

    Args:
        calendar_name: Name of the calendar (empty = default)
        start: Start datetime (ISO 8601). Empty = today 00:00
        end: End datetime (ISO 8601). Empty = today 24:00
    """
    try:
        url, user, pw = _resolve_credentials(ctx)
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        start_dt = _parse_dt(start) if start else datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_dt = _parse_dt(end) if end else (start_dt + timedelta(days=1))
        events = cal.search(start=start_dt, end=end_dt, event=True, expand=True)
        if not events:
            return "No events in range"
        return "\n".join(
            f"- [{d['uid']}] {d['summary']} @ {d['dtstart']} -> {d['dtend']}"
            for d in (_event_to_dict(e) for e in events)
        )
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def caldav_get_today_events(calendar_name: str = "", ctx: Context = None) -> str:
    """Get events for today (00:00 to 24:00)."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return caldav_get_events(
        calendar_name=calendar_name,
        start=today.isoformat(),
        end=(today + timedelta(days=1)).isoformat(),
        ctx=ctx,
    )


@mcp.tool()
def caldav_get_week_events(calendar_name: str = "", ctx: Context = None) -> str:
    """Get events for the next 7 days."""
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return caldav_get_events(
        calendar_name=calendar_name,
        start=now.isoformat(),
        end=(now + timedelta(days=7)).isoformat(),
        ctx=ctx,
    )


@mcp.tool()
def caldav_get_event_by_uid(uid: str, calendar_name: str = "", ctx: Context = None) -> str:
    """
    Get a specific event by its UID.

    Args:
        uid: Event UID
        calendar_name: Name of the calendar (empty = default)
    """
    try:
        url, user, pw = _resolve_credentials(ctx)
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        d = _event_to_dict(event)
        return (
            f"UID: {d['uid']}\n"
            f"Summary: {d['summary']}\n"
            f"Start: {d['dtstart']}\n"
            f"End: {d['dtend']}\n"
            f"Location: {d['location']}\n"
            f"Description: {d['description']}\n"
            f"Categories: {d['categories']}"
        )
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def caldav_create_event(
    summary: str,
    start: str,
    end: str = "",
    calendar_name: str = "",
    location: str = "",
    description: str = "",
    categories: str = "",
    priority: str = "",
    rrule: str = "",
    ctx: Context = None,
) -> str:
    """
    Create a new calendar event.

    Args:
        summary: Event title/summary
        start: Start datetime (ISO 8601)
        end: End datetime (ISO 8601, optional; defaults to start + 1 hour)
        calendar_name: Name of the calendar (empty = default)
        location: Optional location
        description: Optional description
        categories: Optional comma-separated categories/tags
        priority: Optional priority (0-9, 0 = highest)
        rrule: Optional recurrence rule (e.g. FREQ=WEEKLY;BYDAY=MO)
    """
    try:
        url, user, pw = _resolve_credentials(ctx)
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        start_dt = _parse_dt(start)
        end_dt = _parse_dt(end) if end else (start_dt + timedelta(hours=1))

        uid = f"{uuid.uuid4()}@caldav-mcp"
        ical_parts = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//caldav-mcp//EN",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{_format_ical_dt(datetime.now(timezone.utc))}",
            f"DTSTART:{_format_ical_dt(start_dt)}",
            f"DTEND:{_format_ical_dt(end_dt)}",
            f"SUMMARY:{summary}",
        ]
        if location:
            ical_parts.append(f"LOCATION:{location}")
        if description:
            ical_parts.append(f"DESCRIPTION:{description}")
        if categories:
            ical_parts.append(f"CATEGORIES:{categories}")
        if priority:
            ical_parts.append(f"PRIORITY:{priority}")
        if rrule:
            ical_parts.append(f"RRULE:{rrule}")
        ical_parts.extend(["END:VEVENT", "END:VCALENDAR"])

        cal.save_event("\r\n".join(ical_parts) + "\r\n")
        return f"OK: Event '{summary}' created (uid={uid})"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def caldav_update_event(
    uid: str,
    summary: str = "",
    start: str = "",
    end: str = "",
    calendar_name: str = "",
    location: str = "",
    description: str = "",
    ctx: Context = None,
) -> str:
    """
    Update an existing event by UID.

    Only the fields provided (non-empty) are updated.

    Args:
        uid: Event UID (required)
        summary: New summary (optional)
        start: New start datetime (ISO 8601, optional)
        end: New end datetime (ISO 8601, optional)
        calendar_name: Name of the calendar (empty = default)
        location: New location (optional)
        description: New description (optional)
    """
    try:
        url, user, pw = _resolve_credentials(ctx)
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        ical = event.icalendar_instance
        vevent = ical.vobject_instance.vevent

        if summary:
            vevent.summary.value = summary
        if start:
            vevent.dtstart.value = _parse_dt(start)
        if end:
            vevent.dtend.value = _parse_dt(end)
        if location:
            vevent.location.value = location
        if description:
            vevent.description.value = description

        event.save()
        return f"OK: Event {uid} updated"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def caldav_delete_event(uid: str, calendar_name: str = "", ctx: Context = None) -> str:
    """
    Delete an event by UID.

    Args:
        uid: Event UID
        calendar_name: Name of the calendar (empty = default)
    """
    try:
        url, user, pw = _resolve_credentials(ctx)
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        event.delete()
        return f"OK: Deleted event {uid}"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def caldav_search_events(query: str, calendar_name: str = "", ctx: Context = None) -> str:
    """
    Search events by text (summary/description/location).

    Args:
        query: Search text
        calendar_name: Name of the calendar (empty = default)
    """
    try:
        url, user, pw = _resolve_credentials(ctx)
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        events = cal.search()
        q = query.lower()
        matches = []
        for event in events:
            d = _event_to_dict(event)
            blob = " ".join([d["summary"], d["description"], d["location"], d["categories"]]).lower()
            if q in blob:
                matches.append(d)
        if not matches:
            return f"No events matching '{query}'"
        return "\n".join(f"- [{d['uid']}] {d['summary']} @ {d['dtstart']}" for d in matches)
    except Exception as e:
        return f"ERROR: {e}"


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main():
    import asyncio

    asyncio.run(
        mcp.run_http_async(
            host="0.0.0.0",
            port=DEFAULT_PORT,
            transport="streamable-http",
            path=DEFAULT_PATH,
        )
    )


if __name__ == "__main__":
    main()
