import os
import uuid
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from caldav import DAVClient
from icalendar import Calendar, Event
from icalendar import vCalAddress, vText
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

DEFAULT_PORT = int(os.environ.get("CALDAV_MCP_PORT", "8080"))
DEFAULT_PATH = os.environ.get("CALDAV_MCP_PATH", "/mcp")

API_KEY = os.environ.get("CALDAV_MCP_API_KEY", "")

HDR_URL = "x-caldav-url"
HDR_USERNAME = "x-caldav-username"
HDR_PASSWORD = "x-caldav-password"
HDR_AUTHORIZATION = "authorization"
HDR_API_KEY = "x-api-key"


def _server_tz() -> timezone:
    """Return the configured server timezone.

    Reads the TZ environment variable (e.g. 'Europe/Vienna'); falls back to
    UTC when TZ is unset, empty, or invalid.
    """
    tz_name = os.environ.get("TZ", "").strip()
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            pass
    return timezone.utc


SERVER_TZ = _server_tz()


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


def _const_eq(a: str, b: str) -> bool:
    """Constant-time string comparison to avoid timing attacks on the token."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


def _require_auth() -> str:
    """Enforce the shared API token, if configured.

    Returns an empty string on success, or an auth error string to return to the
    client when authentication fails. Authentication is disabled (returns "") when
    CALDAV_MCP_API_KEY is not set.
    """
    expected = API_KEY
    if not expected:
        return ""

    headers = get_http_headers()
    provided = ""
    auth = headers.get(HDR_AUTHORIZATION, "")
    if auth:
        scheme, _, token = auth.partition(" ")
        if scheme.lower() == "bearer":
            provided = token.strip()
    if not provided:
        provided = headers.get(HDR_API_KEY, "").strip()

    if provided and _const_eq(provided, expected):
        return ""
    return "ERROR: unauthorized - missing or invalid API token"


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


def _now():
    """Return the current time in the server timezone."""
    return datetime.now(SERVER_TZ)


def _start_of_day(dt):
    """Return the local midnight (start of day) for the given datetime in the server timezone."""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_dt(value):
    value = value.strip()
    if not value:
        return _now()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=SERVER_TZ)
            return dt
        except ValueError:
            continue
    raise ValueError("Could not parse datetime: %r" % value)


def _format_ical_dt(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _text(comp, name):
    """Extract a text value from an icalendar component property."""
    v = comp.get(name)
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ",".join(_text_single(x) for x in v)
    return _text_single(v)


def _text_single(v):
    # icalendar prop objects; handle vText / vDDDTypes / plain str
    try:
        dt = v.dt
    except AttributeError:
        dt = None
    if dt is not None:
        if isinstance(dt, datetime):
            return dt.isoformat()
        return str(dt)
    return str(v)


def _comp(event):
    """Return the icalendar.Component for a caldav Event object."""
    return getattr(event, "icalendar_component", None)


def _event_to_dict(event):
    comp = _comp(event)
    if comp is None:
        return {
            "uid": getattr(event, "id", ""),
            "summary": "",
            "dtstart": "",
            "dtend": "",
            "location": "",
            "description": "",
            "categories": "",
            "attendees": "",
        }

    attendee_val = comp.get("attendee")
    attendee_str = ""
    if isinstance(attendee_val, (list, tuple)):
        attendee_str = "; ".join(_attendee_str(a) for a in attendee_val)
    elif attendee_val is not None:
        attendee_str = _attendee_str(attendee_val)

    return {
        "uid": _text(comp, "uid") or getattr(event, "id", ""),
        "summary": _text(comp, "summary"),
        "dtstart": _text(comp, "dtstart"),
        "dtend": _text(comp, "dtend"),
        "location": _text(comp, "location"),
        "description": _text(comp, "description"),
        "categories": _text(comp, "categories"),
        "attendees": attendee_str,
    }


def _attendee_str(attendee):
    email = str(attendee)
    # vCalAddress: value is 'mailto:user@host'
    role = getattr(attendee, "params", {})
    r = ""
    p = ""
    if isinstance(role, dict):
        r = str(role.get("ROLE", ""))
        p = str(role.get("PARTSTAT", ""))
    bits = [email]
    if r:
        bits.append("ROLE=" + r)
    if p:
        bits.append("PARTSTAT=" + p)
    return " ".join(bits)


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
    """Get events in a date range for a calendar."""
    try:
        url, user, pw = _resolve_credentials()
        client = _client(url, user, pw)
        cal = _get_calendar(client, calendar_name or None)
        start_dt = _parse_dt(start) if start else _start_of_day(_now())
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
    today = _start_of_day(_now())
    return caldav_get_events(
        calendar_name=calendar_name,
        start=today.isoformat(),
        end=(today + timedelta(days=1)).isoformat(),
    )


@mcp.tool()
def caldav_get_week_events(calendar_name: str = "") -> str:
    """Get events for the next 7 days."""
    now = _start_of_day(_now())
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

        ical = Calendar()
        ical.add("prodid", "-//caldav-mcp//EN")
        ical.add("version", "2.0")

        event = Event()
        event.add("uid", uid)
        event.add("dtstamp", datetime.now(timezone.utc))
        event.add("dtstart", start_dt)
        event.add("dtend", end_dt)
        event.add("summary", summary)

        if location:
            event.add("location", location)
        if description:
            event.add("description", description)
        if categories:
            event.add("categories", categories)

        if priority:
            try:
                priority_int = int(priority)
            except (TypeError, ValueError):
                return "ERROR: priority must be an integer"
            if not 0 <= priority_int <= 9:
                return "ERROR: priority must be between 0 and 9"
            event.add("priority", priority_int)

        if rrule:
            try:
                from icalendar.prop import vRecur

                parsed_rrule = vRecur.from_ical(rrule)
            except Exception:
                return "ERROR: invalid RRULE"
            if not parsed_rrule:
                # e.g. "garbage" parses to an empty vRecur; a valid recur
                # requires at least a frequency.
                return "ERROR: invalid RRULE"
            event.add("rrule", rrule)

        if attendees:
            for email in attendees.split(","):
                email = email.strip()
                if not email:
                    continue
                attendee = vCalAddress("mailto:" + email)
                attendee.params["PARTSTAT"] = vText("NEEDS-ACTION")
                attendee.params["RSVP"] = vText("TRUE")
                attendee.params["ROLE"] = vText("REQ-PARTICIPANT")
                event.add("attendee", attendee, encode=False)

        ical.add_component(event)
        cal.save_event(ical.to_ical().decode("utf-8"))
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
        from icalendar import Calendar, Event as IEvent
        comp = _comp(event)
        if comp is None:
            return "ERROR: no icalendar component"
        if summary:
            comp["SUMMARY"] = summary
        if start:
            comp["DTSTART"] = _parse_dt(start)
        if end:
            comp["DTEND"] = _parse_dt(end)
        if location:
            comp["LOCATION"] = location
        if description:
            comp["DESCRIPTION"] = description
        event.data = comp.to_ical().decode("utf-8")
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
        target = "mailto:" + email
        data = event.data
        if target not in data:
            return "Attendee %s not found on event %s" % (email, uid)
        new_lines = []
        for line in data.splitlines():
            ul = line.upper()
            if ul.startswith("ATTENDEE") and target in line:
                continue
            new_lines.append(line)
        event.data = "\r\n".join(new_lines)
        event.save()
        return "OK: Removed attendee %s from event %s" % (email, uid)
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
        start_dt = _parse_dt(start) if start else _start_of_day(_now())
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
