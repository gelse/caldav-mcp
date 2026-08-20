"""Event mutation tool handlers: create, update, delete, move."""

import uuid
from datetime import timedelta

from caldav import DAVClient  # type: ignore[attr-defined]
from icalendar import Calendar, Event, vCalAddress, vText
from icalendar.prop import vRecur

from caldav_mcp import mcp
from caldav_mcp.auth import _resolve_credentials
from caldav_mcp.calendar import (
    _comp,
    _event_to_dict,
    _get_calendar,
    _validate_priority,
    _validate_rrule,
)
from caldav_mcp.client_cache import client_cache
from caldav_mcp.datetime_utils import _now, _parse_dt
from caldav_mcp.errors import Status, ToolResult
from caldav_mcp.tools import _REMOTE_ERRORS, _ok, _render_error, with_caldav_client


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
):
    """Create a new calendar event."""
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
            return ToolResult.failure(Status.ERROR, err)
        event.add("priority", priority_int)

    if rrule:
        if not _validate_rrule(rrule):
            return ToolResult.failure(Status.ERROR, "invalid RRULE")
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
):
    """Update an existing event by UID. Only provided fields are updated."""
    event = cal.event_by_uid(uid)
    comp = _comp(event)
    if comp is None:
        return ToolResult.failure(Status.ERROR, "no icalendar component")
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
    return _ok(message=f"Event {uid} updated", data={"uid": uid})


@mcp.tool()
@with_caldav_client()
def caldav_delete_event(client, cal, uid: str, calendar_name: str = ""):
    """Delete an event by UID."""
    event = cal.event_by_uid(uid)
    event.delete()
    return _ok(message=f"Deleted event {uid}", data={"uid": uid})


@mcp.tool()
def caldav_move_event(uid: str, target_calendar: str, source_calendar: str = ""):
    """Move an event to another calendar (copy to target with new UID, delete original)."""
    try:
        from caldav_mcp.auth import _require_auth

        error = _require_auth()
        if error:
            return error
        url, user, pw = _resolve_credentials()

        client = client_cache.get(url, user)
        if client is None:
            client = DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
            client_cache.put(url, user, client)

        src_cal = _get_calendar(client, source_calendar or None)
        dst_cal = _get_calendar(client, target_calendar)
        event = src_cal.event_by_uid(uid)
        comp = _comp(event)
        if comp is None:
            return ToolResult.failure(Status.ERROR, "no icalendar component")
        new_uid = f"{uuid.uuid4()}@caldav-mcp"
        comp["UID"] = new_uid
        dst_cal.save_event(comp.to_ical().decode("utf-8"))
        event.delete()
        return _ok(
            message=f"Moved event {uid} -> {target_calendar} (new uid={new_uid})",
            data={"uid": uid, "new_uid": new_uid, "target_calendar": target_calendar},
        )
    except _REMOTE_ERRORS as e:
        return _render_error(e, "caldav_move_event")
