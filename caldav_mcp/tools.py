"""MCP tool handlers for the caldav-mcp server.

All HTTP-facing tool handlers live here.  They are decorated with
``@mcp.tool()`` and register themselves on the shared ``mcp`` instance created
in :mod:`caldav_mcp`.

Every handler returns a structured :class:`caldav_mcp.errors.ToolResult` rather
than a hand-formatted string: outcomes are expressed via the typed ``status``
field (see :class:`caldav_mcp.errors.Status`), and human-readable text lives in
``message``.  Callers and tests branch on ``result.status`` / ``result.data``
instead of parsing error prefixes.

To stay compatible with the existing test suite, which patches attributes on the
``server`` module (``mock.patch.object(server, "<name>", ...)``) and expects
those patches to be observed, every call to shared runtime state (helpers,
``DAVClient``, ``ToolResult``) is routed through the ``server`` namespace rather
than imported directly.
"""

import inspect
import uuid
from datetime import timedelta

from icalendar import Calendar, Event, vCalAddress, vText
from icalendar.prop import vRecur

import server
from caldav_mcp import mcp
from caldav_mcp.errors import ToolResult


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ok(message: str = "", data=None) -> ToolResult:
    return server.ToolResult.success(message=message, data=data)


def _empty(message: str = "") -> ToolResult:
    return server.ToolResult.empty(message=message)


def with_caldav_client(needs_calendar=True):
    """Decorator that handles auth, client creation, and error classification.

    The wrapped function receives:

    * ``client`` – a live :class:`caldav.DAVClient` instance.
    * ``cal`` – a resolved calendar object (*only* when *needs_calendar* is
      ``True``).
    * all other keyword arguments passed by the caller.

    Routing through ``server.*`` keeps existing ``mock.patch.object(server, ...)``
    patches working so no test changes are required.

    The wrapper exposes the original function's signature **minus** the
    injected ``client``/``cal`` parameters so that FastMCP can derive the
    JSON-schema for the tool and static analysers do not flag callers that
    omit those parameters.
    """

    def decorator(fn):
        # Build a public signature: original params minus 'client' and 'cal'.
        sig = inspect.signature(fn)
        public_params = [
            p for name, p in sig.parameters.items()
            if name != "client" and (not needs_calendar or name != "cal")
        ]

        def wrapper(*_args, **kwargs):
            try:
                error = server._require_auth()
                if error:
                    return error
                url, user, pw = server._resolve_credentials()
                client = server.DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
                if needs_calendar:
                    cal = server._get_calendar(client, kwargs.get("calendar_name") or None)
                    return fn(client=client, cal=cal, **kwargs)
                return fn(client=client, **kwargs)
            except Exception as e:
                return server._render_error(e, fn.__name__)

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        wrapper.__annotations__ = {
            k: v for k, v in fn.__annotations__.items()
            if k != "return" and (k != "client") and (not needs_calendar or k != "cal")
        }
        wrapper.__annotations__["return"] = fn.__annotations__.get("return")
        wrapper.__signature__ = sig.replace(parameters=public_params)  # type: ignore[attr-defined]
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

@mcp.tool()
@with_caldav_client(needs_calendar=False)
def caldav_list_calendars(client) -> ToolResult:
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
) -> ToolResult:
    """Get events in a date range for a calendar."""
    start_dt = server._parse_dt(start) if start else server._start_of_day(server._now())
    end_dt = server._parse_dt(end) if end else (start_dt + timedelta(days=1))
    events = cal.search(start=start_dt, end=end_dt, event=True, expand=True)
    data = [server._event_to_dict(e) for e in events]
    if not data:
        return _empty("No events in range")
    return _ok(
        message="\n".join(
            f"- [{d['uid']}] {d['summary']} @ {d['dtstart']} -> {d['dtend']}"
            for d in data
        ),
        data=data,
    )


@mcp.tool()
def caldav_get_today_events(calendar_name: str = "") -> ToolResult:
    """Get events for today (00:00 to 24:00)."""
    error = server._require_auth()
    if error:
        return error
    today = server._start_of_day(server._now())
    return server.caldav_get_events(  # type: ignore[call-arg]
        calendar_name=calendar_name,
        start=today.isoformat(),
        end=(today + timedelta(days=1)).isoformat(),
    )


@mcp.tool()
def caldav_get_week_events(calendar_name: str = "") -> ToolResult:
    """Get events for the next 7 days."""
    error = server._require_auth()
    if error:
        return error
    now = server._start_of_day(server._now())
    return server.caldav_get_events(  # type: ignore[call-arg]
        calendar_name=calendar_name,
        start=now.isoformat(),
        end=(now + timedelta(days=7)).isoformat(),
    )


@mcp.tool()
@with_caldav_client()
def caldav_get_event_by_uid(client, cal, uid: str, calendar_name: str = "") -> ToolResult:
    """Get a specific event by its UID."""
    event = cal.event_by_uid(uid)
    d = server._event_to_dict(event)
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
def caldav_create_event(
    client,
    cal,
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
) -> ToolResult:
    """Create a new calendar event."""
    start_dt = server._parse_dt(start)
    end_dt = server._parse_dt(end) if end else (start_dt + timedelta(hours=1))

    uid = f"{uuid.uuid4()}@caldav-mcp"

    ical = Calendar()
    ical.add("prodid", "-//caldav-mcp//EN")
    ical.add("version", "2.0")

    event = Event()
    event.add("uid", uid)
    event.add("dtstamp", server._now())
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
        priority_int, err = server._validate_priority(priority)
        if err:
            return server.ToolResult.failure(server.Status.ERROR, err)
        event.add("priority", priority_int)

    if rrule:
        if not server._validate_rrule(rrule):
            return server.ToolResult.failure(server.Status.ERROR, "invalid RRULE")
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
    return _ok(
        message=f"Event '{summary}' created (uid={uid})",
        data={"uid": uid, "summary": summary},
    )


@mcp.tool()
@with_caldav_client()
def caldav_update_event(
    client,
    cal,
    uid: str,
    summary: str = "",
    start: str = "",
    end: str = "",
    calendar_name: str = "",
    location: str = "",
    description: str = "",
) -> ToolResult:
    """Update an existing event by UID. Only provided fields are updated."""
    event = cal.event_by_uid(uid)
    comp = server._comp(event)
    if comp is None:
        return server.ToolResult.failure(server.Status.ERROR, "no icalendar component")
    if summary:
        comp["SUMMARY"] = summary
    if start:
        comp["DTSTART"] = server._parse_dt(start)
    if end:
        comp["DTEND"] = server._parse_dt(end)
    if location:
        comp["LOCATION"] = location
    if description:
        comp["DESCRIPTION"] = description
    event.data = comp.to_ical().decode("utf-8")
    event.save()
    return _ok(message=f"Event {uid} updated", data={"uid": uid})


@mcp.tool()
@with_caldav_client()
def caldav_add_attendee(
    client,
    cal,
    uid: str,
    email: str,
    calendar_name: str = "",
    role: str = "REQ-PARTICIPANT",
) -> ToolResult:
    """Add an attendee to an existing event."""
    event = cal.event_by_uid(uid)
    comp = server._comp(event)
    if comp is None:
        return server.ToolResult.failure(server.Status.ERROR, "no icalendar component")
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
    return _ok(
        message=f"Added attendee {email} to event {uid}", data={"uid": uid, "email": email}
    )


@mcp.tool()
@with_caldav_client()
def caldav_remove_attendee(
    client, cal, uid: str, email: str, calendar_name: str = ""
) -> ToolResult:
    """Remove an attendee from an existing event."""
    event = cal.event_by_uid(uid)
    comp = server._comp(event)
    if comp is None:
        return server.ToolResult.failure(server.Status.ERROR, "no icalendar component")
    target = email.strip()
    if not target.lower().startswith("mailto:"):
        target = "mailto:" + target
    target_norm = target.lower()

    current = comp.get("attendee")
    if current is None:
        return server.ToolResult.failure(
            server.Status.NOT_FOUND, f"Attendee {email} not found on event {uid}"
        )
    if not isinstance(current, (list, tuple)):
        current = [current]

    remaining = [a for a in current if str(a).strip().lower() != target_norm]
    if len(remaining) == len(current):
        return server.ToolResult.failure(
            server.Status.NOT_FOUND, f"Attendee {email} not found on event {uid}"
        )

    if remaining:
        comp["attendee"] = remaining
    else:
        del comp["attendee"]
    event.data = comp.to_ical().decode("utf-8")
    event.save()
    return _ok(
        message=f"Removed attendee {email} from event {uid}", data={"uid": uid, "email": email}
    )


@mcp.tool()
@with_caldav_client()
def caldav_list_attendees(
    client, cal, uid: str, calendar_name: str = ""
) -> ToolResult:
    """List attendees of an event."""
    event = cal.event_by_uid(uid)
    d = server._event_to_dict(event)
    if not d["attendees"]:
        return _empty("No attendees")
    attendees = d["attendees"].split("; ")
    return _ok(
        message="\n".join("- " + a for a in attendees),
        data=attendees,
    )


@mcp.tool()
@with_caldav_client()
def caldav_delete_event(
    client, cal, uid: str, calendar_name: str = ""
) -> ToolResult:
    """Delete an event by UID."""
    event = cal.event_by_uid(uid)
    event.delete()
    return _ok(message=f"Deleted event {uid}", data={"uid": uid})


@mcp.tool()
def caldav_move_event(uid: str, target_calendar: str, source_calendar: str = "") -> ToolResult:
    """Move an event to another calendar (copy to target with new UID, delete original)."""
    try:
        error = server._require_auth()
        if error:
            return error
        url, user, pw = server._resolve_credentials()
        client = server.DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        src_cal = server._get_calendar(client, source_calendar or None)
        dst_cal = server._get_calendar(client, target_calendar)
        event = src_cal.event_by_uid(uid)
        comp = server._comp(event)
        if comp is None:
            return server.ToolResult.failure(server.Status.ERROR, "no icalendar component")
        new_uid = f"{uuid.uuid4()}@caldav-mcp"
        comp["UID"] = new_uid
        dst_cal.save_event(comp.to_ical().decode("utf-8"))
        event.delete()
        return _ok(
            message=f"Moved event {uid} -> {target_calendar} (new uid={new_uid})",
            data={"uid": uid, "new_uid": new_uid, "target_calendar": target_calendar},
        )
    except Exception as e:
        return server._render_error(e, "caldav_move_event")


@mcp.tool()
@with_caldav_client()
def caldav_search_events(
    client, cal, query: str, calendar_name: str = ""
) -> ToolResult:
    """Search events by text (summary/description/location)."""
    events = cal.search()
    q = query.lower()
    matches = []
    for event in events:
        d = server._event_to_dict(event)
        blob = " ".join(
            [d["summary"], d["description"], d["location"], d["categories"]]
        ).lower()
        if q in blob:
            matches.append(d)
    if not matches:
        return _empty(f"No events matching '{query}'")
    return _ok(
        message="\n".join(
            f"- [{d['uid']}] {d['summary']} @ {d['dtstart']}" for d in matches
        ),
        data=matches,
    )


@mcp.tool()
@with_caldav_client()
def caldav_get_freebusy(
    client, cal, start: str = "", end: str = "", calendar_name: str = ""
) -> ToolResult:
    """Get free/busy information for a time range."""
    start_dt = server._parse_dt(start) if start else server._start_of_day(server._now())
    end_dt = server._parse_dt(end) if end else (start_dt + timedelta(days=1))
    events = cal.search(start=start_dt, end=end_dt, event=True, expand=True)
    data = [server._event_to_dict(e) for e in events]
    if not data:
        return _ok("Free (no events in range)", data=[])
    lines = [f"Busy ({len(data)} events):"]
    for d in data:
        lines.append(f"- {d['dtstart']} -> {d['dtend']}: {d['summary']}")
    return _ok(message="\n".join(lines), data=data)
