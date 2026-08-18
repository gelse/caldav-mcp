"""MCP tool handlers for the caldav-mcp server.

All HTTP-facing tool handlers live here.  They are decorated with
``@mcp.tool()`` and register themselves on the shared ``mcp`` instance created
in :mod:`caldav_mcp`.

To stay compatible with the existing test suite, which patches attributes on the
``server`` module (``mock.patch.object(server, "<name>", ...)``) and expects
those patches to be observed, every call to shared runtime state (helpers,
``DAVClient``, the exception classes) is routed through the ``server``
namespace rather than imported directly.
"""

import uuid
from datetime import timedelta

from icalendar import Calendar, Event, vCalAddress, vText
from icalendar.prop import vRecur

import server
from caldav_mcp import mcp


@mcp.tool()
def caldav_list_calendars() -> str:
    """List all calendars available for the configured account."""
    try:
        error = server._require_auth()
        if error:
            return error
        url, user, pw = server._resolve_credentials()
        calendars = server.DAVClient(url=url, username=user, password=pw).principal().calendars()  # type: ignore[operator]
        if not calendars:
            return "No calendars found"
        return "\n".join(f"- {c.name} (url: {c.url})" for c in calendars)
    except server.AuthError as e:
        return f"ERROR:[auth] {e}"
    except server.NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return server._log_exception(e, "caldav_list_calendars")


@mcp.tool()
def caldav_get_events(calendar_name: str = "", start: str = "", end: str = "") -> str:
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
        if not events:
            return "No events in range"
        return "\n".join(
            f"- [{d['uid']}] {d['summary']} @ {d['dtstart']} -> {d['dtend']}"
            for d in (server._event_to_dict(e) for e in events)
        )
    except server.AuthError as e:
        return f"ERROR:[auth] {e}"
    except server.NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return server._log_exception(e, "caldav_get_events")


@mcp.tool()
def caldav_get_today_events(calendar_name: str = "") -> str:
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
def caldav_get_week_events(calendar_name: str = "") -> str:
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
def caldav_get_event_by_uid(uid: str, calendar_name: str = "") -> str:
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
    except server.AuthError as e:
        return f"ERROR:[auth] {e}"
    except server.NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return server._log_exception(e, "caldav_get_event_by_uid")


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
                return "ERROR: " + err
            event.add("priority", priority_int)

        if rrule:
            if not server._validate_rrule(rrule):
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
    except server.AuthError as e:
        return f"ERROR:[auth] {e}"
    except server.NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return server._log_exception(e, "caldav_create_event")


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
        error = server._require_auth()
        if error:
            return error
        url, user, pw = server._resolve_credentials()
        client = server.DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cal = server._get_calendar(client, calendar_name or None)
        event = cal.event_by_uid(uid)
        comp = server._comp(event)
        if comp is None:
            return "ERROR: no icalendar component"
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
        return f"OK: Event {uid} updated"
    except server.AuthError as e:
        return f"ERROR:[auth] {e}"
    except server.NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return server._log_exception(e, "caldav_update_event")


@mcp.tool()
def caldav_add_attendee(
    uid: str, email: str, calendar_name: str = "", role: str = "REQ-PARTICIPANT"
) -> str:
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
    except server.AuthError as e:
        return f"ERROR:[auth] {e}"
    except server.NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return server._log_exception(e, "caldav_add_attendee")


@mcp.tool()
def caldav_remove_attendee(uid: str, email: str, calendar_name: str = "") -> str:
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
    except server.AuthError as e:
        return f"ERROR:[auth] {e}"
    except server.NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return server._log_exception(e, "caldav_remove_attendee")


@mcp.tool()
def caldav_list_attendees(uid: str, calendar_name: str = "") -> str:
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
            return "No attendees"
        return "\n".join("- " + a for a in d["attendees"].split("; "))
    except server.AuthError as e:
        return f"ERROR:[auth] {e}"
    except server.NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return server._log_exception(e, "caldav_list_attendees")


@mcp.tool()
def caldav_delete_event(uid: str, calendar_name: str = "") -> str:
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
        return f"OK: Deleted event {uid}"
    except server.AuthError as e:
        return f"ERROR:[auth] {e}"
    except server.NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return server._log_exception(e, "caldav_delete_event")


@mcp.tool()
def caldav_move_event(uid: str, target_calendar: str, source_calendar: str = "") -> str:
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
            return "ERROR: no icalendar component"
        new_uid = f"{uuid.uuid4()}@caldav-mcp"
        comp["UID"] = new_uid
        dst_cal.save_event(comp.to_ical().decode("utf-8"))
        event.delete()
        return f"OK: Moved event {uid} -> {target_calendar} (new uid={new_uid})"
    except server.AuthError as e:
        return f"ERROR:[auth] {e}"
    except server.NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return server._log_exception(e, "caldav_move_event")


@mcp.tool()
def caldav_search_events(query: str, calendar_name: str = "") -> str:
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
            return f"No events matching '{query}'"
        return "\n".join(
            f"- [{d['uid']}] {d['summary']} @ {d['dtstart']}" for d in matches
        )
    except server.AuthError as e:
        return f"ERROR:[auth] {e}"
    except server.NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return server._log_exception(e, "caldav_search_events")


@mcp.tool()
def caldav_get_freebusy(start: str = "", end: str = "", calendar_name: str = "") -> str:
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
        if not events:
            return "Free (no events in range)"
        lines = [f"Busy ({len(events)} events):"]
        for e in events:
            d = server._event_to_dict(e)
            lines.append(f"- {d['dtstart']} -> {d['dtend']}: {d['summary']}")
        return "\n".join(lines)
    except server.AuthError as e:
        return f"ERROR:[auth] {e}"
    except server.NotFoundError as e:
        return f"ERROR:[not_found] {e}"
    except Exception as e:
        return server._log_exception(e, "caldav_get_freebusy")
