"""Attendee management tool handlers."""

from icalendar import vCalAddress, vText

from caldav_mcp import mcp
from caldav_mcp.calendar import _comp, _event_to_dict
from caldav_mcp.constants import (
    DEFAULT_ATTENDEE_ROLE,
    DEFAULT_PARTSTAT,
    DEFAULT_RSVP,
    ERR_NO_COMPONENT,
    MAILTO_PREFIX,
)
from caldav_mcp.errors import Status, ToolResult
from caldav_mcp.tools import _empty, _ok, with_caldav_client

# Attendee add/remove modify the event but are additive, not destructive.
_WRITE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
# List attendees is read-only.
_RO_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


@mcp.tool(annotations=_WRITE_ANNOTATIONS)
@with_caldav_client()
def caldav_add_attendee(
    client,
    cal,
    uid: str,
    email: str,
    calendar_name: str = "",
    role: str = DEFAULT_ATTENDEE_ROLE,
):
    """Add an attendee to an existing event."""
    from caldav_mcp.sanitizers import validate_email

    # Strip mailto: prefix before validation (users may pass "mailto:user@example.com").
    email_for_validation = email.strip()
    if email_for_validation.lower().startswith(MAILTO_PREFIX):
        email_for_validation = email_for_validation[len(MAILTO_PREFIX) :]
    validate_email(email_for_validation)

    event = cal.event_by_uid(uid)
    comp = _comp(event)
    if comp is None:
        return ToolResult.failure(Status.ERROR, ERR_NO_COMPONENT)
    email_clean = email.strip()
    if not email_clean.lower().startswith(MAILTO_PREFIX):
        email_clean = MAILTO_PREFIX + email_clean
    attendee = vCalAddress(email_clean)
    attendee.params["PARTSTAT"] = vText(DEFAULT_PARTSTAT)
    attendee.params["RSVP"] = vText(DEFAULT_RSVP)
    attendee.params["ROLE"] = vText(role)
    comp.add("attendee", attendee, encode=False)
    event.data = comp.to_ical().decode("utf-8")
    event.save()
    return _ok(message=f"Added attendee {email} to event {uid}", data={"uid": uid, "email": email})


@mcp.tool(annotations=_WRITE_ANNOTATIONS)
@with_caldav_client()
def caldav_remove_attendee(client, cal, uid: str, email: str, calendar_name: str = ""):
    """Remove an attendee from an existing event."""
    event = cal.event_by_uid(uid)
    comp = _comp(event)
    if comp is None:
        return ToolResult.failure(Status.ERROR, ERR_NO_COMPONENT)
    target = email.strip()
    if not target.lower().startswith(MAILTO_PREFIX):
        target = MAILTO_PREFIX + target
    target_norm = target.lower()

    current = comp.get("attendee")
    if current is None:
        return ToolResult.failure(Status.NOT_FOUND, f"Attendee {email} not found on event {uid}")
    if not isinstance(current, (list, tuple)):
        current = [current]

    remaining = [a for a in current if str(a).strip().lower() != target_norm]
    if len(remaining) == len(current):
        return ToolResult.failure(Status.NOT_FOUND, f"Attendee {email} not found on event {uid}")

    if remaining:
        comp["attendee"] = remaining
    else:
        del comp["attendee"]
    event.data = comp.to_ical().decode("utf-8")
    event.save()
    return _ok(
        message=f"Removed attendee {email} from event {uid}", data={"uid": uid, "email": email}
    )


@mcp.tool(annotations=_RO_ANNOTATIONS)
@with_caldav_client()
def caldav_list_attendees(client, cal, uid: str, calendar_name: str = ""):
    """List attendees of an event."""
    event = cal.event_by_uid(uid)
    d = _event_to_dict(event)
    if not d["attendees"]:
        return _empty("No attendees")
    attendees = d["attendees"].split("; ")
    return _ok(
        message="\n".join("- " + a for a in attendees),
        data=attendees,
    )
