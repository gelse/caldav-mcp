"""iCalendar event construction helpers.

Pure functions that build icalendar ``Calendar`` and ``Event`` objects from
flat parameters.  Extracted from ``caldav_create_event`` to improve
testability and readability.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from icalendar import Calendar, Event, vCalAddress, vText
from icalendar.prop import vRecur

from caldav_mcp.constants import (
    DEFAULT_ATTENDEE_ROLE,
    DEFAULT_PARTSTAT,
    DEFAULT_RSVP,
    ICAL_VERSION,
    MAILTO_PREFIX,
    PRODID,
    UID_DOMAIN,
)


def build_event(
    summary: str,
    start_dt: datetime,
    end_dt: datetime,
    now: datetime,
    *,
    location: str = "",
    description: str = "",
    categories: str = "",
    priority_int: int | None = None,
    rrule_parsed: vRecur | None = None,
    attendee_emails: list[str] | None = None,
) -> tuple[Calendar, str]:
    """Build a complete iCalendar Calendar containing one VEVENT.

    Returns
    -------
    tuple[Calendar, str]
        The Calendar object and the generated UID string.
    """
    uid = f"{uuid.uuid4()}@{UID_DOMAIN}"

    ical = Calendar()
    ical.add("prodid", PRODID)
    ical.add("version", ICAL_VERSION)

    event = Event()
    event.add("uid", uid)
    event.add("dtstamp", now)
    event.add("dtstart", start_dt)
    event.add("dtend", end_dt)
    event.add("summary", summary)

    if location:
        event.add("location", location)
    if description:
        event.add("description", description)
    if categories:
        event.add("categories", categories)
    if priority_int is not None:
        event.add("priority", priority_int)
    if rrule_parsed is not None:
        event.add("rrule", rrule_parsed)

    if attendee_emails:
        for email in attendee_emails:
            email = email.strip()
            if not email:
                continue
            attendee = vCalAddress(MAILTO_PREFIX + email)
            attendee.params["PARTSTAT"] = vText(DEFAULT_PARTSTAT)
            attendee.params["RSVP"] = vText(DEFAULT_RSVP)
            attendee.params["ROLE"] = vText(DEFAULT_ATTENDEE_ROLE)
            event.add("attendee", attendee, encode=False)

    ical.add_component(event)
    return ical, uid


def parse_attendee_emails(attendees_str: str) -> list[str]:
    """Split a comma-separated attendee string into cleaned email list."""
    if not attendees_str:
        return []
    return [e.strip() for e in attendees_str.split(",") if e.strip()]
