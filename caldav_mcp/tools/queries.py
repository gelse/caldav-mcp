"""Read-only calendar and event query tool handlers."""

from datetime import timedelta

from caldav_mcp import mcp
from caldav_mcp.calendar import _event_to_dict, _get_calendar
from caldav_mcp.datetime_utils import _now, _parse_dt, _start_of_day
from caldav_mcp.errors import NotFoundError, Status, ToolResult
from caldav_mcp.sanitizers import MAX_QUERY_LENGTH, sanitize_text
from caldav_mcp.tools import _empty, _ok, with_caldav_fanout

# All query tools are read-only — shared annotation.
_RO_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


# ---------------------------------------------------------------------------
# Per-calendar core functions (shared by simple mode and fan-out)
# ---------------------------------------------------------------------------


def _list_calendars_core(client):
    """Return the list of calendars for *client* as a list of dicts."""
    calendars = client.principal().calendars()
    return [{"name": c.name, "url": str(c.url)} for c in calendars]


def _get_events_core(cal, start: str = "", end: str = "", calendar_name: str = ""):
    """Return events in a date range for a single calendar."""
    start_dt = _parse_dt(start) if start else _start_of_day(_now())
    end_dt = _parse_dt(end) if end else (start_dt + timedelta(days=1))
    events = cal.search(start=start_dt, end=end_dt, event=True, expand=True)
    return [_event_to_dict(e) for e in events]


def _get_event_by_uid_core(cal, uid: str):
    """Return event dict for *uid* on a single calendar, or None."""
    try:
        event = cal.event_by_uid(uid)
    except NotFoundError:
        return None
    return _event_to_dict(event)


def _search_events_core(cal, query: str):
    """Search events by text on a single calendar; return matching dicts."""
    query = sanitize_text(query, MAX_QUERY_LENGTH)
    events = cal.search()
    q = query.lower()
    matches = []
    for event in events:
        d = _event_to_dict(event)
        blob = " ".join([d["summary"], d["description"], d["location"], d["categories"]]).lower()
        if q in blob:
            matches.append(d)
    return matches


def _get_freebusy_core(cal, start: str = "", end: str = ""):
    """Return free/busy data for a single calendar."""
    start_dt = _parse_dt(start) if start else _start_of_day(_now())
    end_dt = _parse_dt(end) if end else (start_dt + timedelta(days=1))
    events = cal.search(start=start_dt, end=end_dt, event=True, expand=True)
    return [_event_to_dict(e) for e in events]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


@mcp.tool(annotations=_RO_ANNOTATIONS)
@with_caldav_fanout(needs_calendar=False, once_per_remote=True)
def caldav_list_calendars(client):
    """List all calendars available for the configured account."""
    calendars = _list_calendars_core(client)
    if not calendars:
        return _empty("No calendars found")
    return _ok(
        message="\n".join(f"- {c['name']} (url: {c['url']})" for c in calendars),
        data=calendars,
    )


@mcp.tool(annotations=_RO_ANNOTATIONS)
@with_caldav_fanout()
def caldav_get_events(client, cal, calendar_name: str = "", start: str = "", end: str = ""):
    """Get events in a date range for a calendar."""
    data = _get_events_core(cal, start=start, end=end, calendar_name=calendar_name)
    if not data:
        return _empty("No events in range")
    return _ok(
        message="\n".join(
            f"- [{d['uid']}] {d['summary']} @ {d['dtstart']} -> {d['dtend']}" for d in data
        ),
        data=data,
    )


@mcp.tool(annotations=_RO_ANNOTATIONS)
@with_caldav_fanout(needs_calendar=False, once_per_remote=True)
def caldav_get_today_events(client, calendar_name: str = "", cal=None):
    """Get events for today (00:00 to 24:00).

    Pro mode fans out once per remote: the injected ``cal`` (fan-out) or the
    simple-mode fallback (single remote) is the calendar actually queried, so
    each calendar's own events are returned under its own entry.
    """
    if cal is None:
        cal = _get_calendar(client, calendar_name or None)
    today = _start_of_day(_now())
    start_iso = today.isoformat()
    end_iso = (today + timedelta(days=1)).isoformat()
    data = _get_events_core(
        cal,
        start=start_iso,
        end=end_iso,
        calendar_name=calendar_name,
    )
    if not data:
        return _empty("No events for today")
    return _ok(
        message="\n".join(
            f"- [{d['uid']}] {d['summary']} @ {d['dtstart']} -> {d['dtend']}" for d in data
        ),
        data=data,
    )


@mcp.tool(annotations=_RO_ANNOTATIONS)
@with_caldav_fanout(needs_calendar=False, once_per_remote=True)
def caldav_get_week_events(client, calendar_name: str = "", cal=None):
    """Get events for the next 7 days.

    Pro mode fans out once per remote (see :func:`caldav_get_today_events`).
    """
    if cal is None:
        cal = _get_calendar(client, calendar_name or None)
    now = _start_of_day(_now())
    start_iso = now.isoformat()
    end_iso = (now + timedelta(days=7)).isoformat()
    data = _get_events_core(
        cal,
        start=start_iso,
        end=end_iso,
        calendar_name=calendar_name,
    )
    if not data:
        return _empty("No events in the next 7 days")
    return _ok(
        message="\n".join(
            f"- [{d['uid']}] {d['summary']} @ {d['dtstart']} -> {d['dtend']}" for d in data
        ),
        data=data,
    )


@mcp.tool(annotations=_RO_ANNOTATIONS)
@with_caldav_fanout()
def caldav_get_event_by_uid(client, cal, uid: str, calendar_name: str = ""):
    """Get a specific event by its UID."""
    d = _get_event_by_uid_core(cal, uid)
    if d is None:
        # Preserve the historical simple-mode contract: a UID that is not on
        # the calendar is a typed NOT_FOUND failure (previously propagated as
        # NotFoundError and classified by the decorator).  In pro-mode fan-out
        # this per-calendar NOT_FOUND is an absence and is treated as a
        # successful (empty) entry by ``_execute_fanout``.
        return ToolResult.failure(Status.NOT_FOUND, f"Event '{uid}' not found")
    return _ok(
        message=(
            "UID: " + d["uid"] + "\n"
            "Summary: " + d["summary"] + "\n"
            "Start: " + d["dtstart"] + "\n"
            "End: " + d["dtend"] + "\n"
            "Location: " + d["location"] + "\n"
            "Description: " + d["description"] + "\n"
            "Categories: " + d["categories"] + "\n"
            "Attendees: " + d["attendees"]
        ),
        data=d,
    )


@mcp.tool(annotations=_RO_ANNOTATIONS)
@with_caldav_fanout()
def caldav_search_events(client, cal, query: str, calendar_name: str = ""):
    """Search events by text in summary, description, or location.

    This tool performs a full-text search across all events on the calendar.
    It does NOT accept date range parameters (start/end).

    For date-range queries, use caldav_get_events instead, which filters
    events by start and end times.
    """
    matches = _search_events_core(cal, query)
    if not matches:
        return _empty(f"No events matching '{query}'")
    return _ok(
        message="\n".join(f"- [{d['uid']}] {d['summary']} @ {d['dtstart']}" for d in matches),
        data=matches,
    )


@mcp.tool(annotations=_RO_ANNOTATIONS)
@with_caldav_fanout()
def caldav_get_freebusy(client, cal, start: str = "", end: str = "", calendar_name: str = ""):
    """Get free/busy information for a time range."""
    data = _get_freebusy_core(cal, start=start, end=end)
    if not data:
        return _ok("Free (no events in range)", data=[])
    lines = [f"Busy ({len(data)} events):"]
    for d in data:
        lines.append(f"- {d['dtstart']} -> {d['dtend']}: {d['summary']}")
    return _ok(message="\n".join(lines), data=data)
