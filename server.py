import os
import uuid
import asyncio
from datetime import datetime, timedelta, timezone

from caldav import DAVClient
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_PORT = int(os.environ.get("CALDAV_MCP_PORT", "8080"))
DEFAULT_PATH = os.environ.get("CALDAV_MCP_PATH", "/mcp")

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


class CalDAVError(Exception):
    pass


def _resolve_credentials() -> tuple:
    headers = get_http_headers()
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


def _client(url, username, password):
    return DAVClient(url=url, username=username, password=password)


def _get_calendar(client, calendar_name=""):
    calendars = client.principal().calendars()
    if not calendars:
        raise ValueError("No calendars found for this principal")
    if calendar_name:
        for c in calendars:
            if c.name == calendar_name:
                return c
        raise ValueError(
            "Calendar '%s' not found. Available: " % calendar_name
            + ", ".join(c.name for c in calendars)
        )
    return calendars[0]


def _parse_dt(value):
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
    raise ValueError("Could not parse datetime: %r" % value)


def _format_ical_dt(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _event_to_dict(event):
    ical = event.icalendar_instance
    vevent = ical.vobject_instance.vevent
    summary = getattr(vevent, "summary", None)
    dtstart = getattr(vevent, "dtstart", None)
    dtend = getattr(vevent, "dtend", None)
    uid_attr = getattr(vevent, "uid", None)
    categories = getattr(vevent, "categories", None)
    attendees = getattr(vevent, "attendee", None)
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
        "attendees": (
            "; ".join(_attendee_str(a) for a in attendees)
            if isinstance(attendees, (list, tuple))
            else ""
        ),
    }


def _attendee_str(attendee):
    email = attendee.value if hasattr(attendee, "value") else str(attendee)
    role = getattr(attendee, "role_param", None) or getattr(attendee, "role", None) or ""
    partstat = getattr(attendee, "partstat_param", None) or ""
    bits = [email]
    if role:
        bits.append("ROLE=" + role)
    if partstat:
        bits.append("PARTSTAT=" + partstat)
    return " ".join(bits)


def _get_vevent(event):
    return event.icalendar_instance.vobject_instance.vevent


@mcp.tool()
def caldav_list_calendars() -> str:
    """List all calendars available for the configured account."""
    try:
        url, user, pw = _resolve_credentials()
        calendars = _client(url, user, pw).principal().calendars()
        if not calendars:
            return "No calendars found"
        return "\n".join("- %s (url: %s)" % (c.name, c.url) for c in calendars)
    except Exception as e:
        return "ERROR: %s" % e


@mcp.tool()
def caldav_get_events(calendar_name: str = "", start: str = "", end: str = "") -> str:
    """Get events in a date range for a calendar.
    Args: calendar_name (empty=default), start (ISO 8601, empty=today 00:00), end (ISO 8601, empty=today 24:00)
    """
    try:
        url, user, pw = _resolve_credentials()
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
            "- [%s] %s @ %s -> %s" % (d["uid"], d["summary"], d["dtstart"], d["dtend"])
            for d in (_event_to_dict(e) for e in events)
        )
    except Exception as e:
        return "ERROR: %s" % e


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
    """Get a specific event by its UID."""
    try:
        url, user, pw = _resolve_credentials()
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        d = _event_to_dict(event)
        return (
            "UID: " + d["uid"] + "\n"
            "Summary: " + d["summary"] + "\n"
            "Start: " + d["dtstart"] + "\n"
            "End: " + d["dtend"] + "\n"
            "Location: " + d["location"] + "\n"
            "Description: " + d["description"] + "\n"
            "Categories: " + d["categories"] + "\n"
            "Attendees: " + d["attendees"]
        )
    except Exception as e:
        return "ERROR: %s" % e


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
    attendees: str = "",
) -> str:
    """Create a new calendar event."""
    try:
        url, user, pw = _resolve_credentials()
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        start_dt = _parse_dt(start)
        end_dt = _parse_dt(end) if end else (start_dt + timedelta(hours=1))

        uid = "%s@caldav-mcp" % uuid.uuid4()
        ical_parts = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//caldav-mcp//EN",
            "BEGIN:VEVENT",
            "UID:" + uid,
            "DTSTAMP:" + _format_ical_dt(datetime.now(timezone.utc)),
            "DTSTART:" + _format_ical_dt(start_dt),
            "DTEND:" + _format_ical_dt(end_dt),
            "SUMMARY:" + summary,
        ]
        if location:
            ical_parts.append("LOCATION:" + location)
        if description:
            ical_parts.append("DESCRIPTION:" + description)
        if categories:
            ical_parts.append("CATEGORIES:" + categories)
        if priority:
            ical_parts.append("PRIORITY:" + priority)
        if rrule:
            ical_parts.append("RRULE:" + rrule)
        if attendees:
            for email in attendees.split(","):
                email = email.strip()
                if email:
                    ical_parts.append("ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:" + email)
        ical_parts.extend(["END:VEVENT", "END:VCALENDAR"])

        cal.save_event("\r\n".join(ical_parts) + "\r\n")
        return "OK: Event '%s' created (uid=%s)" % (summary, uid)
    except Exception as e:
        return "ERROR: %s" % e


@mcp.tool()
def caldav_update_event(
    uid: str,
    summary: str = "",
    start: str = "",
    end: str = "",
    calendar_name: str = "",
    location: str = "",
    description: str = "",
) -> str:
    """Update an existing event by UID. Only provided fields are updated."""
    try:
        url, user, pw = _resolve_credentials()
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        vevent = _get_vevent(event)
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
        return "OK: Event %s updated" % uid
    except Exception as e:
        return "ERROR: %s" % e


@mcp.tool()
def caldav_add_attendee(uid: str, email: str, calendar_name: str = "", role: str = "REQ-PARTICIPANT") -> str:
    """Add an attendee to an existing event."""
    try:
        url, user, pw = _resolve_credentials()
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        attendee_line = "ATTENDEE;ROLE=%s;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:%s" % (role, email)
        data = event.data
        if "END:VEVENT" in data:
            data = data.replace("END:VEVENT", attendee_line + "\r\nEND:VEVENT", 1)
        else:
            data = data + "\r\n" + attendee_line + "\r\n"
        event.data = data
        event.save()
        return "OK: Added attendee %s to event %s" % (email, uid)
    except Exception as e:
        return "ERROR: %s" % e


@mcp.tool()
def caldav_remove_attendee(uid: str, email: str, calendar_name: str = "") -> str:
    """Remove an attendee from an existing event."""
    try:
        url, user, pw = _resolve_credentials()
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        vevent = _get_vevent(event)
        attendees = getattr(vevent, "attendee", None)
        if isinstance(attendees, (list, tuple)):
            target = "mailto:" + email
            if not any(getattr(a, "value", "") == target for a in attendees):
                return "Attendee %s not found on event %s" % (email, uid)
            data = event.data
            new_lines = []
            for line in data.splitlines():
                ul = line.upper()
                if ul.startswith("ATTENDEE") and target in line:
                    continue
                new_lines.append(line)
            event.data = "\r\n".join(new_lines)
            event.save()
            return "OK: Removed attendee %s from event %s" % (email, uid)
        return "No attendees on event %s" % uid
    except Exception as e:
        return "ERROR: %s" % e


@mcp.tool()
def caldav_list_attendees(uid: str, calendar_name: str = "") -> str:
    """List attendees of an event."""
    try:
        url, user, pw = _resolve_credentials()
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        d = _event_to_dict(event)
        if not d["attendees"]:
            return "No attendees"
        return "\n".join("- " + a for a in d["attendees"].split("; "))
    except Exception as e:
        return "ERROR: %s" % e


@mcp.tool()
def caldav_delete_event(uid: str, calendar_name: str = "") -> str:
    """Delete an event by UID."""
    try:
        url, user, pw = _resolve_credentials()
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        event.delete()
        return "OK: Deleted event %s" % uid
    except Exception as e:
        return "ERROR: %s" % e


@mcp.tool()
def caldav_move_event(uid: str, target_calendar: str, source_calendar: str = "") -> str:
    """Move an event to another calendar (copy to target with new UID, delete original)."""
    try:
        url, user, pw = _resolve_credentials()
        client = _client(url, user, pw)
        src_cal = _get_calendar(client, source_calendar or None)
        dst_cal = _get_calendar(client, target_calendar)
        event = src_cal.event_by_uid(uid)
        data = event.data
        new_uid = "%s@caldav-mcp" % uuid.uuid4()
        data = data.replace("UID:" + uid, "UID:" + new_uid, 1)
        dst_cal.save_event(data)
        event.delete()
        return "OK: Moved event %s -> %s (new uid=%s)" % (uid, target_calendar, new_uid)
    except Exception as e:
        return "ERROR: %s" % e


@mcp.tool()
def caldav_search_events(query: str, calendar_name: str = "") -> str:
    """Search events by text (summary/description/location)."""
    try:
        url, user, pw = _resolve_credentials()
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
            return "No events matching '%s'" % query
        return "\n".join("- [%s] %s @ %s" % (d["uid"], d["summary"], d["dtstart"]) for d in matches)
    except Exception as e:
        return "ERROR: %s" % e


@mcp.tool()
def caldav_get_freebusy(start: str = "", end: str = "", calendar_name: str = "") -> str:
    """Get free/busy information for a time range."""
    try:
        url, user, pw = _resolve_credentials()
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        start_dt = _parse_dt(start) if start else datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_dt = _parse_dt(end) if end else (start_dt + timedelta(days=1))
        events = cal.search(start=start_dt, end=end_dt, event=True, expand=True)
        if not events:
            return "Free (no events in range)"
        lines = ["Busy (%d events):" % len(events)]
        for e in events:
            d = _event_to_dict(e)
            lines.append("- %s -> %s: %s" % (d["dtstart"], d["dtend"], d["summary"]))
        return "\n".join(lines)
    except Exception as e:
        return "ERROR: %s" % e


def main():
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
