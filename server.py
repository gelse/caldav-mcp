import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

from caldav import DAVClient  # type: ignore[attr-defined]
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers
from icalendar import Calendar, Event, vCalAddress, vText
from icalendar.prop import vRecur

DEFAULT_PORT = int(os.environ.get("CALDAV_MCP_PORT", "8080"))
DEFAULT_PATH = os.environ.get("CALDAV_MCP_PATH", "/mcp")

API_KEY = os.environ.get("CALDAV_MCP_API_KEY", "")

HDR_URL = "x-caldav-url"
HDR_USERNAME = "x-caldav-username"
HDR_PASSWORD = "x-caldav-password"
HDR_AUTHORIZATION = "authorization"
HDR_API_KEY = "x-api-key"


def _server_tz() -> tzinfo:
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
    return UTC


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
    """Base class for all caldav-mcp operational errors."""


class AuthError(CalDAVError):
    """Raised when CalDAV credentials are missing or invalid."""


class NotFoundError(CalDAVError):
    """Raised when a requested calendar or event does not exist."""


class ServerError(CalDAVError):
    """Raised for unexpected internal faults that should be logged server-side."""


log = logging.getLogger("caldav-mcp")


def _log_exception(exc: Exception, context: str) -> str:
    """Log an unexpected exception with traceback and return a safe client message.

    The returned message must never include credential material or raw exception
    strings that might leak secrets.
    """
    log.exception("Unhandled error in %s", context)
    return "ERROR:[server] Internal error"


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
        raise AuthError(
            "Missing CalDAV credentials. Provide X-Caldav-Url, X-Caldav-Username, "
            "X-Caldav-Password headers, or set CALDAV_URL/CALDAV_USERNAME/"
            "CALDAV_PASSWORD environment variables."
        )
    return url, username, password


def _get_calendar(client, calendar_name=None):
    calendars = client.principal().calendars()
    if not calendars:
        raise NotFoundError("No calendars found for this principal")
    if calendar_name:
        for c in calendars:
            if c.name == calendar_name:
                return c
        raise NotFoundError(
            f"Calendar '{calendar_name}' not found. Available: "
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
    raise ValueError(f"Could not parse datetime: {value!r}")


def _format_ical_dt(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


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


def _validate_priority(priority: str) -> tuple[int | None, str | None]:
    """Validate a priority value.

    Return `(priority_int, None)` on success, or `(None, error_message)` on failure.
    An empty value is skipped and returns `(None, None)`.
    """
    if not priority:
        return None, None
    try:
        priority_int = int(priority)
    except (TypeError, ValueError):
        return None, "priority must be an integer"
    if not 0 <= priority_int <= 9:
        return None, "priority must be between 0 and 9"
    return priority_int, None


def _validate_rrule(rrule: str) -> bool:
    """Return True if rrule is empty or a valid recurrence rule, False otherwise."""
    if not rrule:
        return True
    try:
        parsed_rrule = vRecur.from_ical(rrule)
    except Exception:
        return False
    if not parsed_rrule:
        # e.g. "garbage" parses to an empty vRecur; a valid recur requires
        # at least a frequency.
        return False
    return True


@mcp.tool()
def caldav_list_calendars() -> str:
    """List all calendars available for the configured account."""
    try:
        error = _require_auth()
        if error:
            return error
        url, user, pw = _resolve_credentials()
        calendars = DAVClient(url=url, username=user, password=pw).principal().calendars()  # type: ignore[operator]
        if not calendars:
            return "No calendars found"
        return "\n".join(f"- {c.name} (url: {c.url})" for c in calendars)
    except AuthError as e:
        return f"ERROR:[auth] {e}"
    except NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return _log_exception(e, "caldav_list_calendars")


@mcp.tool()
def caldav_get_events(calendar_name: str = "", start: str = "", end: str = "") -> str:
    """Get events in a date range for a calendar."""
    try:
        error = _require_auth()
        if error:
            return error
        url, user, pw = _resolve_credentials()
        client = DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = _get_calendar(client, calendar_name or None)
        start_dt = _parse_dt(start) if start else _start_of_day(_now())
        end_dt = _parse_dt(end) if end else (start_dt + timedelta(days=1))
        events = cal.search(start=start_dt, end=end_dt, event=True, expand=True)
        if not events:
            return "No events in range"
        return "\n".join(
            f"- [{d['uid']}] {d['summary']} @ {d['dtstart']} -> {d['dtend']}"
            for d in (_event_to_dict(e) for e in events)
        )
    except AuthError as e:
        return f"ERROR:[auth] {e}"
    except NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return _log_exception(e, "caldav_get_events")


@mcp.tool()
def caldav_get_today_events(calendar_name: str = "") -> str:
    """Get events for today (00:00 to 24:00)."""
    error = _require_auth()
    if error:
        return error
    today = _start_of_day(_now())
    return caldav_get_events(
        calendar_name=calendar_name,
        start=today.isoformat(),
        end=(today + timedelta(days=1)).isoformat(),
    )


@mcp.tool()
def caldav_get_week_events(calendar_name: str = "") -> str:
    """Get events for the next 7 days."""
    error = _require_auth()
    if error:
        return error
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
        error = _require_auth()
        if error:
            return error
        url, user, pw = _resolve_credentials()
        client = DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
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
    except AuthError as e:
        return f"ERROR:[auth] {e}"
    except NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return _log_exception(e, "caldav_get_event_by_uid")


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
        error = _require_auth()
        if error:
            return error
        url, user, pw = _resolve_credentials()
        client = DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = _get_calendar(client, calendar_name or None)
        start_dt = _parse_dt(start)
        end_dt = _parse_dt(end) if end else (start_dt + timedelta(hours=1))

        uid = f"{uuid.uuid4()}@caldav-mcp"

        ical = Calendar()
        ical.add("prodid", "-//caldav-mcp//EN")
        ical.add("version", "2.0")

        event = Event()
        event.add("uid", uid)
        event.add("dtstamp", _now())
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
            priority_int, err = _validate_priority(priority)
            if err:
                return "ERROR: " + err
            event.add("priority", priority_int)

        if rrule:
            if not _validate_rrule(rrule):
                return "ERROR: invalid RRULE"
            # Add the parsed vRecur so the recurrence value is typed/escaped via
            # the component API rather than injected as a raw string.
            event.add("rrule", vRecur.from_ical(rrule))

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
        return f"OK: Event '{summary}' created (uid={uid})"
    except AuthError as e:
        return f"ERROR:[auth] {e}"
    except NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return _log_exception(e, "caldav_create_event")


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
        error = _require_auth()
        if error:
            return error
        url, user, pw = _resolve_credentials()
        client = DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = _get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
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
        return f"OK: Event {uid} updated"
    except AuthError as e:
        return f"ERROR:[auth] {e}"
    except NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return _log_exception(e, "caldav_update_event")


@mcp.tool()
def caldav_add_attendee(
    uid: str, email: str, calendar_name: str = "", role: str = "REQ-PARTICIPANT"
) -> str:
    """Add an attendee to an existing event."""
    try:
        error = _require_auth()
        if error:
            return error
        url, user, pw = _resolve_credentials()
        client = DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = _get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        comp = _comp(event)
        if comp is None:
            return "ERROR: no icalendar component"
        email_clean = email.strip()
        if not email_clean.lower().startswith("mailto:"):
            email_clean = "mailto:" + email_clean
        attendee = vCalAddress(email_clean)
        attendee.params["PARTSTAT"] = vText("NEEDS-ACTION")
        attendee.params["RSVP"] = vText("TRUE")
        attendee.params["ROLE"] = vText(role)
        comp.add("attendee", attendee, encode=False)
        event.data = comp.to_ical().decode("utf-8")
        event.save()
        return f"OK: Added attendee {email} to event {uid}"
    except AuthError as e:
        return f"ERROR:[auth] {e}"
    except NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return _log_exception(e, "caldav_add_attendee")


@mcp.tool()
def caldav_remove_attendee(uid: str, email: str, calendar_name: str = "") -> str:
    """Remove an attendee from an existing event."""
    try:
        error = _require_auth()
        if error:
            return error
        url, user, pw = _resolve_credentials()
        client = DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = _get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        comp = _comp(event)
        if comp is None:
            return "ERROR: no icalendar component"
        target = email.strip()
        if not target.lower().startswith("mailto:"):
            target = "mailto:" + target
        target_norm = target.lower()

        current = comp.get("attendee")
        if current is None:
            return f"Attendee {email} not found on event {uid}"
        if not isinstance(current, (list, tuple)):
            current = [current]

        remaining = [a for a in current if str(a).strip().lower() != target_norm]
        if len(remaining) == len(current):
            return f"Attendee {email} not found on event {uid}"

        if remaining:
            comp["attendee"] = remaining
        else:
            del comp["attendee"]
        event.data = comp.to_ical().decode("utf-8")
        event.save()
        return f"OK: Removed attendee {email} from event {uid}"
    except AuthError as e:
        return f"ERROR:[auth] {e}"
    except NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return _log_exception(e, "caldav_remove_attendee")


@mcp.tool()
def caldav_list_attendees(uid: str, calendar_name: str = "") -> str:
    """List attendees of an event."""
    try:
        error = _require_auth()
        if error:
            return error
        url, user, pw = _resolve_credentials()
        client = DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = _get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        d = _event_to_dict(event)
        if not d["attendees"]:
            return "No attendees"
        return "\n".join("- " + a for a in d["attendees"].split("; "))
    except AuthError as e:
        return f"ERROR:[auth] {e}"
    except NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return _log_exception(e, "caldav_list_attendees")


@mcp.tool()
def caldav_delete_event(uid: str, calendar_name: str = "") -> str:
    """Delete an event by UID."""
    try:
        error = _require_auth()
        if error:
            return error
        url, user, pw = _resolve_credentials()
        client = DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = _get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        event.delete()
        return f"OK: Deleted event {uid}"
    except AuthError as e:
        return f"ERROR:[auth] {e}"
    except NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return _log_exception(e, "caldav_delete_event")


@mcp.tool()
def caldav_move_event(uid: str, target_calendar: str, source_calendar: str = "") -> str:
    """Move an event to another calendar (copy to target with new UID, delete original)."""
    try:
        error = _require_auth()
        if error:
            return error
        url, user, pw = _resolve_credentials()
        client = DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        src_cal = _get_calendar(client, source_calendar or None)
        dst_cal = _get_calendar(client, target_calendar)
        event = src_cal.event_by_uid(uid)
        comp = _comp(event)
        if comp is None:
            return "ERROR: no icalendar component"
        new_uid = f"{uuid.uuid4()}@caldav-mcp"
        comp["UID"] = new_uid
        dst_cal.save_event(comp.to_ical().decode("utf-8"))
        event.delete()
        return f"OK: Moved event {uid} -> {target_calendar} (new uid={new_uid})"
    except AuthError as e:
        return f"ERROR:[auth] {e}"
    except NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return _log_exception(e, "caldav_move_event")


@mcp.tool()
def caldav_search_events(query: str, calendar_name: str = "") -> str:
    """Search events by text (summary/description/location)."""
    try:
        error = _require_auth()
        if error:
            return error
        url, user, pw = _resolve_credentials()
        client = DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = _get_calendar(client, calendar_name or None)
        events = cal.search()
        q = query.lower()
        matches = []
        for event in events:
            d = _event_to_dict(event)
            blob = " ".join(
                [d["summary"], d["description"], d["location"], d["categories"]]
            ).lower()
            if q in blob:
                matches.append(d)
        if not matches:
            return f"No events matching '{query}'"
        return "\n".join(
            f"- [{d['uid']}] {d['summary']} @ {d['dtstart']}" for d in matches
        )
    except AuthError as e:
        return f"ERROR:[auth] {e}"
    except NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return _log_exception(e, "caldav_search_events")


@mcp.tool()
def caldav_get_freebusy(start: str = "", end: str = "", calendar_name: str = "") -> str:
    """Get free/busy information for a time range."""
    try:
        error = _require_auth()
        if error:
            return error
        url, user, pw = _resolve_credentials()
        client = DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = _get_calendar(client, calendar_name or None)
        start_dt = _parse_dt(start) if start else _start_of_day(_now())
        end_dt = _parse_dt(end) if end else (start_dt + timedelta(days=1))
        events = cal.search(start=start_dt, end=end_dt, event=True, expand=True)
        if not events:
            return "Free (no events in range)"
        lines = [f"Busy ({len(events)} events):"]
        for e in events:
            d = _event_to_dict(e)
            lines.append(f"- {d['dtstart']} -> {d['dtend']}: {d['summary']}")
        return "\n".join(lines)
    except AuthError as e:
        return f"ERROR:[auth] {e}"
    except NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return _log_exception(e, "caldav_get_freebusy")


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
