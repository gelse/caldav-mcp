"""CalDAV client/calendar access and event serialization helpers.

Pure helpers (``_text``, ``_event_to_dict``, ``_validate_priority``, …) and the
``_get_calendar`` selector live here.  Errors are imported directly from
:mod:`caldav_mcp.errors`, eliminating the previous circular ``import server``
dependency.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from icalendar.prop import vRecur

from caldav_mcp.errors import NotFoundError
from caldav_mcp.types import CalDAVClient


def _get_calendar(client: CalDAVClient, calendar_name: str | None = None) -> Any:
    """Select a calendar from the principal's calendar list.

    When *calendar_name* is provided the matching calendar is returned;
    otherwise the first calendar is used as the default.  Raises
    :class:`NotFoundError` with a list of available calendar names when
    the requested name is not found.

    Parameters
    ----------
    client : caldav.DAVClient
        An authenticated DAV client instance.
    calendar_name : str or None
        Optional calendar name to select.  ``None`` selects the first.
    """
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


def _text(comp: Any, name: str) -> str:
    """Extract a text value from an icalendar component property."""
    v = comp.get(name)
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ",".join(_text_single(x) for x in v)
    return _text_single(v)


def _text_single(v: Any) -> str:
    """Extract a plain string from a single icalendar property value.

    Handles ``vText``, ``vDDDTypes`` (which carry a ``.dt`` attribute),
    and plain ``str`` objects.  Datetime values are serialized to ISO 8601.
    """
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


def _comp(event: Any) -> Any | None:
    """Return the icalendar.Component for a caldav Event object."""
    return getattr(event, "icalendar_component", None)


def _event_to_dict(event: Any) -> dict[str, str]:
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

    # Attendees require special handling: icalendar may return a single
    # vCalAddress, a list, or None.  We normalize to a "; "-separated string.
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


def _attendee_str(attendee: Any) -> str:
    """Format a ``vCalAddress`` attendee as a human-readable string.

    Returns a space-separated string containing the email address and
    optional ``ROLE`` / ``PARTSTAT`` parameters, e.g.::

        mailto:alice@example.com ROLE=REQ-PARTICIPANT PARTSTAT=ACCEPTED
    """
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
        # A string like "garbage" parses to an empty vRecur object without
        # raising — we must check for the presence of a FREQ property.
        return False
    return True
