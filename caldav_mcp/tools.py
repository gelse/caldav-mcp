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

import uuid
from datetime import timedelta

from icalendar import Calendar, Event, vCalAddress, vText
from icalendar.prop import vRecur

import server
from caldav_mcp import mcp
from caldav_mcp.errors import ToolResult


def _ok(message: str = "", data=None) -> ToolResult:
    return server.ToolResult.success(message=message, data=data)


def _empty(message: str = "") -> ToolResult:
    return server.ToolResult.empty(message=message)


@mcp.tool()
def caldav_list_calendars() -> ToolResult:
    """List all calendars available for the configured account."""
    try:
        error = server._require_auth()
        if error:
            return error
        url, user, pw = server._resolve_credentials()
        calendars = server.DAVClient(url=url, username=user, password=pw).principal().calendars()  # type: ignore[operator]
        if not calendars:
            return _empty("No calendars found")
        return _ok(
            message="\n".join(f"- {c.name} (url: {c.url})" for c in calendars),
            data=[{"name": c.name, "url": str(c.url)} for c in calendars],
        )
    except Exception as e:
        return server._render_error(e, "caldav_list_calendars")


@mcp.tool()
def caldav_get_events(
    calendar_name: str = "", start: str = "", end: str = ""
) -> ToolResult:
    """Get events in a date range for a calendar."""
    try:
        error = server._require_auth()
        if error:
            return error
        url, user, pw = server._resolve_credentials()
        client = server.DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = server._get_calendar(client, calendar_name or None)
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
    except Exception as e:
        return server._render_error(e, "caldav_get_events")


@mcp.tool()
def caldav_get_today_events(calendar_name: str = "") -> ToolResult:
    """Get events for today (00:00 to 24:00)."""
    error = server._require_auth()
    if error:
        return error
    today = server._start_of_day(server._now())
    return server.caldav_get_events(
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
    return server.caldav_get_events(
        calendar_name=calendar_name,
        start=now.isoformat(),
        end=(now + timedelta(days=7)).isoformat(),
    )


@mcp.tool()
def caldav_get_event_by_uid(uid: str, calendar_name: str = "") -> ToolResult:
    """Get a specific event by its UID."""
    try:
        error = server._require_auth()
        if error:
            return error
        url, user, pw = server._resolve_credentials()
        client = server.DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = server._get_calendar(client, calendar_name or None)
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
    except Exception as e:
        return server._render_error(e, "caldav_get_event_by_uid")


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
) -> ToolResult:
    """Create a new calendar event."""
    try:
        error = server._require_auth()
        if error:
            return error
        url, user, pw = server._resolve_credentials()
        client = server.DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = server._get_calendar(client, calendar_name or None)
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
    except Exception as e:
        return server._render_error(e, "caldav_create_event")


@mcp.tool()
def caldav_update_event(
    uid: str,
    summary: str = "",
    start: str = "",
    end: str = "",
    calendar_name: str = "",
    location: str = "",
    description: str = "",
) -> ToolResult:
    """Update an existing event by UID. Only provided fields are updated."""
    try:
        error = server._require_auth()
        if error:
            return error
        url, user, pw = server._resolve_credentials()
        client = server.DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = server._get_calendar(client, calendar_name or None)
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
    except Exception as e:
        return server._render_error(e, "caldav_update_event")


@mcp.tool()
def caldav_add_attendee(
    uid: str, email: str, calendar_name: str = "", role: str = "REQ-PARTICIPANT"
) -> ToolResult:
    """Add an attendee to an existing event."""
    try:
        error = server._require_auth()
        if error:
            return error
        url, user, pw = server._resolve_credentials()
        client = server.DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = server._get_calendar(client, calendar_name or None)
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
    except Exception as e:
        return server._render_error(e, "caldav_add_attendee")


@mcp.tool()
def caldav_remove_attendee(uid: str, email: str, calendar_name: str = "") -> ToolResult:
    """Remove an attendee from an existing event."""
    try:
        error = server._require_auth()
        if error:
            return error
        url, user, pw = server._resolve_credentials()
        client = server.DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = server._get_calendar(client, calendar_name or None)
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
    except Exception as e:
        return server._render_error(e, "caldav_remove_attendee")


@mcp.tool()
def caldav_list_attendees(uid: str, calendar_name: str = "") -> ToolResult:
    """List attendees of an event."""
    try:
        error = server._require_auth()
        if error:
            return error
        url, user, pw = server._resolve_credentials()
        client = server.DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = server._get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        d = server._event_to_dict(event)
        if not d["attendees"]:
            return _empty("No attendees")
        attendees = d["attendees"].split("; ")
        return _ok(
            message="\n".join("- " + a for a in attendees),
            data=attendees,
        )
    except Exception as e:
        return server._render_error(e, "caldav_list_attendees")


@mcp.tool()
def caldav_delete_event(uid: str, calendar_name: str = "") -> ToolResult:
    """Delete an event by UID."""
    try:
        error = server._require_auth()
        if error:
            return error
        url, user, pw = server._resolve_credentials()
        client = server.DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = server._get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        event.delete()
        return _ok(message=f"Deleted event {uid}", data={"uid": uid})
    except Exception as e:
        return server._render_error(e, "caldav_delete_event")


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
def caldav_search_events(query: str, calendar_name: str = "") -> ToolResult:
    """Search events by text (summary/description/location)."""
    try:
        error = server._require_auth()
        if error:
            return error
        url, user, pw = server._resolve_credentials()
        client = server.DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = server._get_calendar(client, calendar_name or None)
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
    except Exception as e:
        return server._render_error(e, "caldav_search_events")


@mcp.tool()
def caldav_get_freebusy(start: str = "", end: str = "", calendar_name: str = "") -> ToolResult:
    """Get free/busy information for a time range."""
    try:
        error = server._require_auth()
        if error:
            return error
        url, user, pw = server._resolve_credentials()
        client = server.DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = server._get_calendar(client, calendar_name or None)
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
    except Exception as e:
        return server._render_error(e, "caldav_get_freebusy")
