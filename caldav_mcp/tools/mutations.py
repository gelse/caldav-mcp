"""Event mutation tool handlers: create, update, delete, move."""

import uuid
from datetime import timedelta

from icalendar import vRecur

from caldav_mcp import mcp
from caldav_mcp.calendar import (
    _comp,
    _get_calendar,
    _validate_priority,
    _validate_rrule,
)
from caldav_mcp.constants import (
    ERR_INVALID_RRULE,
    ERR_NO_COMPONENT,
    UID_DOMAIN,
)
from caldav_mcp.datetime_utils import _now, _parse_dt
from caldav_mcp.errors import Status, ToolResult
from caldav_mcp.tools import _REMOTE_ERRORS, _ok, _render_error, with_caldav_client

# Create — writes a new resource; not idempotent (repeated calls create duplicates).
_CREATE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
# Update — writes to an existing resource; idempotent when called with the same arguments.
_UPDATE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
# Destructive writes (delete, move) — permanently remove or relocate resources.
_DESTRUCTIVE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}


@mcp.tool(annotations=_CREATE_ANNOTATIONS)
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
    from caldav_mcp.event_builder import build_event
    from caldav_mcp.sanitizers import (
        MAX_CATEGORIES_LENGTH,
        MAX_DESCRIPTION_LENGTH,
        MAX_LOCATION_LENGTH,
        MAX_SUMMARY_LENGTH,
        sanitize_text,
        validate_email,
    )

    summary = sanitize_text(summary, MAX_SUMMARY_LENGTH)
    if location:
        location = sanitize_text(location, MAX_LOCATION_LENGTH)
    if description:
        description = sanitize_text(description, MAX_DESCRIPTION_LENGTH)
    if categories:
        categories = sanitize_text(categories, MAX_CATEGORIES_LENGTH)

    # Validate attendee emails before building the event.
    if attendees:
        from caldav_mcp.event_builder import parse_attendee_emails as _parse_ae

        raw_emails = _parse_ae(attendees)
        for addr in raw_emails:
            validate_email(addr)
        attendee_emails = raw_emails
    else:
        attendee_emails = []

    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end) if end else (start_dt + timedelta(hours=1))

    # Validate priority
    priority_int = None
    if priority:
        priority_int, err = _validate_priority(priority)
        if err:
            return ToolResult.failure(Status.ERROR, err)

    # Validate rrule
    rrule_parsed = None
    if rrule:
        if not _validate_rrule(rrule):
            return ToolResult.failure(Status.ERROR, ERR_INVALID_RRULE)
        rrule_parsed = vRecur.from_ical(rrule)

    ical, uid = build_event(
        summary=summary,
        start_dt=start_dt,
        end_dt=end_dt,
        now=_now(),
        location=location,
        description=description,
        categories=categories,
        priority_int=priority_int,
        rrule_parsed=rrule_parsed,
        attendee_emails=attendee_emails,
    )

    cal.save_event(ical.to_ical().decode("utf-8"))
    return _ok(
        message=f"Event '{summary}' created (uid={uid})",
        data={"uid": uid, "summary": summary},
    )


@mcp.tool(annotations=_UPDATE_ANNOTATIONS)
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
    from caldav_mcp.sanitizers import (
        MAX_DESCRIPTION_LENGTH,
        MAX_LOCATION_LENGTH,
        MAX_SUMMARY_LENGTH,
        sanitize_text,
    )

    if summary:
        summary = sanitize_text(summary, MAX_SUMMARY_LENGTH)
    if location:
        location = sanitize_text(location, MAX_LOCATION_LENGTH)
    if description:
        description = sanitize_text(description, MAX_DESCRIPTION_LENGTH)

    event = cal.event_by_uid(uid)
    comp = _comp(event)
    if comp is None:
        return ToolResult.failure(Status.ERROR, ERR_NO_COMPONENT)
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


@mcp.tool(annotations=_DESTRUCTIVE_ANNOTATIONS)
@with_caldav_client()
def caldav_delete_event(client, cal, uid: str, calendar_name: str = ""):
    """Delete an event by UID."""
    event = cal.event_by_uid(uid)
    event.delete()
    return _ok(message=f"Deleted event {uid}", data={"uid": uid})


@mcp.tool(annotations=_DESTRUCTIVE_ANNOTATIONS)
@with_caldav_client(needs_calendar=False)
def caldav_move_event(
    client,
    uid: str,
    target_calendar: str,
    source_calendar: str = "",
):
    """Move an event to another calendar (copy to target with new UID, delete original).

    Move semantics: we cannot rename a UID in-place on most CalDAV servers,
    so we copy the event with a new UID to the target calendar and delete
    the original.  This is not atomic — a failure after copy leaves a
    duplicate, which is the safer failure mode.
    """
    try:
        src_cal = _get_calendar(client, source_calendar or None)
        dst_cal = _get_calendar(client, target_calendar)
        event = src_cal.event_by_uid(uid)
        comp = _comp(event)
        if comp is None:
            return ToolResult.failure(Status.ERROR, ERR_NO_COMPONENT)
        new_uid = f"{uuid.uuid4()}@{UID_DOMAIN}"
        comp["UID"] = new_uid
        dst_cal.save_event(comp.to_ical().decode("utf-8"))
        event.delete()
        return _ok(
            message=f"Moved event {uid} -> {target_calendar} (new uid={new_uid})",
            data={"uid": uid, "new_uid": new_uid, "target_calendar": target_calendar},
        )
    except _REMOTE_ERRORS as e:
        return _render_error(e, "caldav_move_event")
