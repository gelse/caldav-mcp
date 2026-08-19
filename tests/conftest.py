"""Shared pytest fixtures and CalDAV fakes for the caldav-mcp test suite.

These helpers replace the duplicated ``unittest`` fake classes and network
patching functions that previously lived in several individual test files.
They are deliberately *not* pytest fixtures: each fake is mutable, so tests
instantiate them directly (function-scoped fixtures would require separate
per-test instances anyway, and exposing plain helper functions keeps the
conversion simple and explicit).
"""

from unittest import mock

from icalendar import Calendar, Event, vCalAddress, vText

import server

# ── CalDAV boundary fakes ─────────────────────────────────────────────

class FakeEvent:
    """Stand-in for a caldav Event whose icalendar_component is the VEVENT.

    ``data`` is backed by ``comp.to_ical()`` and is refreshed on ``save()``;
    ``delete()`` records the call for later assertions.
    """

    def __init__(self, ical):
        events = ical.walk("VEVENT")
        self.icalendar_component = events[0]  # _comp reads this attribute
        self.data = self.icalendar_component.to_ical().decode("utf-8")
        self.saves = 0
        self.deleted = False

    def save(self):
        # Re-parse the serialized data so icalendar_component reflects new state.
        parsed = Calendar.from_ical(self.data)
        events = parsed.walk("VEVENT")
        self.icalendar_component = events[0]
        self.saves += 1

    def delete(self):
        self.deleted = True


class FakeCalendar:
    """Stand-in for a caldav Calendar: returns an event by UID, or saves data.

    ``saved`` is a list of payloads written via ``save_event()``. ``last_saved``
    is a convenience accessor for the most recent payload (or ``None`` when
    nothing has been saved yet), which keeps single-payload assertions simple.
    """

    def __init__(self, event=None, name=""):
        self._event = event
        self.name = name
        self.saved = []

    @property
    def last_saved(self):
        if not self.saved:
            return None
        return self.saved[-1]

    def event_by_uid(self, uid):
        return self._event

    def save_event(self, data):
        self.saved.append(data)


class FakePrincipal:
    def __init__(self, calendars):
        self._calendars = calendars

    def calendars(self):
        return self._calendars


class FakeClient:
    """Fake caldav DAVClient whose principal exposes the given calendars."""

    def __init__(self, calendars=None):
        self._calendars = calendars or []

    def principal(self):
        return FakePrincipal(self._calendars)


# ── Helper functions (not fixtures) ───────────────────────────────────

def make_event(uid="test-uid@caldav-mcp", summary="Meeting", attendees=()):
    """Build a Calendar with one VEVENT carrying optional ATTENDEE entries."""
    cal = Calendar()
    cal.add("prodid", "-//caldav-mcp//EN")
    cal.add("version", "2.0")
    ev = Event()
    ev.add("uid", uid)
    ev.add("summary", summary)
    for email in attendees:
        attendee = vCalAddress("mailto:" + email)
        attendee.params["CN"] = vText(email.split("@")[0])
        attendee.params["PARTSTAT"] = vText("ACCEPTED")
        attendee.params["RSVP"] = vText("TRUE")
        attendee.params["ROLE"] = vText("REQ-PARTICIPANT")
        ev.add("attendee", attendee, encode=False)
    cal.add_component(ev)
    return cal


def attendees_of(event):
    """Return the ATTENDEE list from the event's current component."""
    attendees = event.icalendar_component.get("attendee")
    if attendees is None:
        return []
    if not isinstance(attendees, (list, tuple)):
        return [attendees]
    return list(attendees)


def patch_caldav(fake_cal):
    """Patch the CalDAV boundaries so the fake calendar is used directly.

    Returns the started patchers; callers stop them when no longer needed
    (typically via a ``yield`` in a fixture-autouse wrapper or a ``finally``).
    """
    patchers = [
        mock.patch.object(server, "_resolve_credentials", return_value=("u", "p", "w")),
        mock.patch.object(server, "DAVClient", return_value=FakeClient([fake_cal])),
        mock.patch.object(server, "_get_calendar", return_value=fake_cal),
    ]
    for p in patchers:
        p.start()
    return patchers


def patch_caldav_move(src_cal, dst_cal):
    """Patch the CalDAV boundaries routing ``_get_calendar`` by name.

    ``_get_calendar`` is faked to return ``dst_cal`` when the requested name
    matches the destination calendar, otherwise ``src_cal``.
    """
    patchers = [
        mock.patch.object(server, "_resolve_credentials", return_value=("u", "p", "w")),
        mock.patch.object(server, "DAVClient", return_value=FakeClient()),
        mock.patch.object(
            server,
            "_get_calendar",
            side_effect=lambda client, name: dst_cal if name == dst_cal.name else src_cal,
        ),
    ]
    for p in patchers:
        p.start()
    return patchers
