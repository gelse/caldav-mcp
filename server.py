#!/usr/bin/env python3
"""
CalDAV MCP Server

Exposes read/write access to any CalDAV-compatible calendar server
(Nextcloud, ownCloud, iCloud, Fastmail, etc.) via the Model Context Protocol.

Runs as a FastMCP STDIO server. Connection settings are provided via
environment variables (never hardcoded):

    CALDAV_URL       e.g. https://cloud.example.com/remote.php/dav/calendars/user/
    CALDAV_USERNAME  CalDAV username
    CALDAV_PASSWORD  CalDAV password (or app-specific password)
"""

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from caldav import DAVClient
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class CalDAVConfig:
    url: str
    username: str
    password: str


def load_config() -> CalDAVConfig:
    cfg = CalDAVConfig(
        url=os.environ.get("CALDAV_URL", ""),
        username=os.environ.get("CALDAV_USERNAME", ""),
        password=os.environ.get("CALDAV_PASSWORD", ""),
    )
    if not cfg.url or not cfg.username or not cfg.password:
        raise RuntimeError(
            "Missing CALDAV_URL / CALDAV_USERNAME / CALDAV_PASSWORD environment variables"
        )
    return cfg


mcp = FastMCP(
    "caldav-mcp",
    instructions=(
        "CalDAV calendar access (read + write). Use caldav_list_calendars first, "
        "then operate on events by UID. All times are ISO 8601."
    ),
)


def get_client() -> DAVClient:
    cfg = load_config()
    return DAVClient(url=cfg.url, username=cfg.username, password=cfg.password)


def principal(client: DAVClient):
    return client.principal()


def get_calendar(client: DAVClient, calendar_name: str = None):
    """Resolve a calendar by name, or return the default (first) calendar."""
    principal_obj = principal(client)
    calendars = principal_obj.calendars()
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
    """Parse an ISO-ish datetime string with fallback to date-only."""
    value = value.strip()
    if not value:
        return datetime.now(timezone.utc)
    # Strip trailing 'Z' -> UTC
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


def _event_to_dict(event) -> dict:
    """Flatten a caldav Event object into a plain dict for JSON-friendly output."""
    ical = event.icalendar_instance
    vevent = ical.vobject_instance.vevent
    summary = getattr(vevent, "summary", None)
    summary = str(summary.value) if summary is not None else ""
    dtstart = getattr(vevent, "dtstart", None)
    dtend = getattr(vevent, "dtend", None)
    uid_attr = getattr(vevent, "uid", None)
    return {
        "uid": str(uid_attr.value) if uid_attr is not None else event.id,
        "summary": summary,
        "dtstart": str(dtstart.value) if dtstart is not None else "",
        "dtend": str(dtend.value) if dtend is not None else "",
        "location": str(vevent.location.value) if getattr(vevent, "location", None) else "",
        "description": str(vevent.description.value) if getattr(vevent, "description", None) else "",
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def caldav_list_calendars() -> str:
    """List all calendars available for the configured account."""
    try:
        client = get_client()
        calendars = principal(client).calendars()
        if not calendars:
            return "No calendars found"
        lines = []
        for c in calendars:
            lines.append(f"- {c.name} (url: {c.url})")
        return "\n".join(lines)
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def caldav_get_events(calendar_name: str = "", start: str = "", end: str = "") -> str:
    """
    Get events in a date range for a calendar.

    Args:
        calendar_name: Name of the calendar (empty = default calendar)
        start: Start datetime (ISO 8601). Empty = today 00:00
        end: End datetime (ISO 8601). Empty = today 24:00
    """
    try:
        client = get_client()
        cal = get_calendar(client, calendar_name or None)
        start_dt = _parse_dt(start) if start else datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_dt = _parse_dt(end) if end else (start_dt + timedelta(days=1))
        events = cal.search(start=start_dt, end=end_dt, event=True, expand=True)
        if not events:
            return "No events in range"
        return "\n".join(
            f"- [{e_dict['uid']}] {e_dict['summary']} @ {e_dict['dtstart']} -> {e_dict['dtend']}"
            for e_dict in (_event_to_dict(e) for e in events)
        )
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def caldav_get_today_events(calendar_name: str = "") -> str:
    """Get events for today (00:00 to 24:00)."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return caldav_get_events(
        calendar_name=calendar_name,
        start=today.isoformat(),
        end=(today + timedelta(days=1)).isoformat(),
    )


@mcp.tool()
def caldav_get_week_events(calendar_name: str = "") -> str:
    """Get events for the next 7 days."""
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return caldav_get_events(
        calendar_name=calendar_name,
        start=now.isoformat(),
        end=(now + timedelta(days=7)).isoformat(),
    )


@mcp.tool()
def caldav_get_event_by_uid(uid: str, calendar_name: str = "") -> str:
    """
    Get a specific event by its UID.

    Args:
        uid: Event UID
        calendar_name: Name of the calendar (empty = default)
    """
    try:
        client = get_client()
        cal = get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        d = _event_to_dict(event)
        return (
            f"UID: {d['uid']}\n"
            f"Summary: {d['summary']}\n"
            f"Start: {d['dtstart']}\n"
            f"End: {d['dtend']}\n"
            f"Location: {d['location']}\n"
            f"Description: {d['description']}"
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
    """
    try:
        client = get_client()
        cal = get_calendar(client, calendar_name or None)
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
        ical_parts.extend(["END:VEVENT", "END:VCALENDAR"])

        cal.save_event("\r\n".join(ical_parts) + "\r\n")
        return f"OK: Event '{summary}' created (uid={uid})"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def caldav_delete_event(uid: str, calendar_name: str = "") -> str:
    """
    Delete an event by UID.

    Args:
        uid: Event UID
        calendar_name: Name of the calendar (empty = default)
    """
    try:
        client = get_client()
        cal = get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        event.delete()
        return f"OK: Deleted event {uid}"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def caldav_search_events(query: str, calendar_name: str = "") -> str:
    """
    Search events by text (summary/description/location).

    Args:
        query: Search text
        calendar_name: Name of the calendar (empty = default)
    """
    try:
        client = get_client()
        cal = get_calendar(client, calendar_name or None)
        events = cal.search()
        matches = []
        q = query.lower()
        for event in events:
            d = _event_to_dict(event)
            blob = " ".join([d["summary"], d["description"], d["location"]]).lower()
            if q in blob:
                matches.append(d)
        if not matches:
            return f"No events matching '{query}'"
        return "\n".join(
            f"- [{d['uid']}] {d['summary']} @ {d['dtstart']}" for d in matches
        )
    except Exception as e:
        return f"ERROR: {e}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_ical_dt(dt: datetime) -> str:
    """Format as ICAL UTC (YYYYMMDDTHHMMSSZ)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def main():
    mcp.run()


if __name__ == "__main__":
    main()
