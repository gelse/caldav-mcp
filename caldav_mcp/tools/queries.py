"""Read-only calendar and event query tool handlers."""

from datetime import timedelta

from caldav_mcp import mcp
from caldav_mcp.auth import _require_auth
from caldav_mcp.calendar import _event_to_dict
from caldav_mcp.datetime_utils import _now, _parse_dt, _start_of_day
from caldav_mcp.tools import _empty, _ok, with_caldav_client


@mcp.tool()
@with_caldav_client(needs_calendar=False)
def caldav_list_calendars(client):
    """List all calendars available for the configured account."""
    calendars = client.principal().calendars()
    if not calendars:
        return _empty("No calendars found")
    return _ok(
        message="\n".join(f"- {c.name} (url: {c.url})" for c in calendars),
        data=[{"name": c.name, "url": str(c.url)} for c in calendars],
    )


@mcp.tool()
@with_caldav_client()
def caldav_get_events(
    client, cal, calendar_name: str = "", start: str = "", end: str = ""
):
    """Get events in a date range for a calendar."""
    start_dt = _parse_dt(start) if start else _start_of_day(_now())
    end_dt = _parse_dt(end) if end else (start_dt + timedelta(days=1))
    events = cal.search(start=start_dt, end=end_dt, event=True, expand=True)
    data = [_event_to_dict(e) for e in events]
    if not data:
        return _empty("No events in range")
    return _ok(
        message="\n".join(
            f"- [{d['uid']}] {d['summary']} @ {d['dtstart']} -> {d['dtend']}" for d in data
        ),
        data=data,
    )


@mcp.tool()
def caldav_get_today_events(calendar_name: str = ""):
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
def caldav_get_week_events(calendar_name: str = ""):
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
@with_caldav_client()
def caldav_get_event_by_uid(client, cal, uid: str, calendar_name: str = ""):
    """Get a specific event by its UID."""
    event = cal.event_by_uid(uid)
    d = _event_to_dict(event)
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


@mcp.tool()
@with_caldav_client()
def caldav_search_events(client, cal, query: str, calendar_name: str = ""):
    """Search events by text (summary/description/location)."""
    events = cal.search()
    q = query.lower()
    matches = []
    for event in events:
        d = _event_to_dict(event)
        blob = " ".join([d["summary"], d["description"], d["location"], d["categories"]]).lower()
        if q in blob:
            matches.append(d)
    if not matches:
        return _empty(f"No events matching '{query}'")
    return _ok(
        message="\n".join(f"- [{d['uid']}] {d['summary']} @ {d['dtstart']}" for d in matches),
        data=matches,
    )


@mcp.tool()
@with_caldav_client()
def caldav_get_freebusy(
    client, cal, start: str = "", end: str = "", calendar_name: str = ""
):
    """Get free/busy information for a time range."""
    start_dt = _parse_dt(start) if start else _start_of_day(_now())
    end_dt = _parse_dt(end) if end else (start_dt + timedelta(days=1))
    events = cal.search(start=start_dt, end=end_dt, event=True, expand=True)
    data = [_event_to_dict(e) for e in events]
    if not data:
        return _ok("Free (no events in range)", data=[])
    lines = [f"Busy ({len(data)} events):"]
    for d in data:
        lines.append(f"- {d['dtstart']} -> {d['dtend']}: {d['summary']}")
    return _ok(message="\n".join(lines), data=data)
