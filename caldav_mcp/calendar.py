"""CalDAV client/calendar access and event serialization helpers.

Pure helpers (`_text`, `_event_to_dict`, `_validate_priority`, ...) and the
`_get_calendar` selector live here; the HTTP-facing handlers in :mod:`tools`
call the shared run-through-the-:mod:`server` namespace so existing tests that
patch ``server.<name>`` keep working.
"""

from datetime import datetime

from icalendar.prop import vRecur

import server


def _get_calendar(client, calendar_name=None):
    calendars = client.principal().calendars()
    if not calendars:
        raise server.NotFoundError("No calendars found for this principal")
    if calendar_name:
        for c in calendars:
            if c.name == calendar_name:
                return c
        raise server.NotFoundError(
            f"Calendar '{calendar_name}' not found. Available: "
            + ", ".join(c.name for c in calendars)
        )
    return calendars[0]


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
