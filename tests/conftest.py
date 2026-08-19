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

    def __init__(self, ical, uid=None):
        events = ical.walk("VEVENT")
        self.icalendar_component = events[0]  # _comp reads this attribute
        if uid is not None:
            self.icalendar_component["uid"] = uid
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
    """Stand-in for a caldav Calendar: returns events by UID or a search, or saves.

    Events are stored internally as a list (``_events``). ``event_by_uid()``
    looks up events by UID via a pre-built ``_events_by_uid`` dict and raises
    ``NotFoundError`` for unknown UIDs. ``search()`` returns all stored events.
    ``saved`` is a list of payloads written via ``save_event()``. ``last_saved``
    is a convenience accessor for the most recent payload (or ``None`` when
    nothing has been saved yet), which keeps single-payload assertions simple.
    """

    def __init__(self, event=None, name="", url="", events=None):
        if events is not None:
            raw = list(events)
        elif event is not None:
            raw = [event]
        else:
            raw = []
        # Accept either FakeEvent instances or raw icalendar Calendar objects;
        # wrap the latter so every stored event exposes ``icalendar_component``.
        self._events = [self._coerce(e) for e in raw]
        self._event = self._events[0] if self._events else None
        self._events_by_uid = {}
        for ev in self._events:
            uid_comp = getattr(ev, "icalendar_component", None)
            if uid_comp is not None:
                uid_val = uid_comp.get("uid")
                if uid_val is not None:
                    self._events_by_uid[str(uid_val)] = ev
        self.name = name
        self.url = url
        self.saved = []

    @staticmethod
    def _coerce(ev):
        if getattr(ev, "icalendar_component", None) is None:
            return FakeEvent(ev)
        return ev

    @property
    def last_saved(self):
        if not self.saved:
            return None
        return self.saved[-1]

    def event_by_uid(self, uid):
        ev = self._events_by_uid.get(uid)
        if ev is None:
            raise server.NotFoundError(f"Event '{uid}' not found")
        return ev

    def save_event(self, data):
        self.saved.append(data)

    def search(self, **kwargs):
        return list(self._events)


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
    from caldav_mcp.client_cache import client_cache
    client_cache.clear()
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
    from caldav_mcp.client_cache import client_cache
    client_cache.clear()
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
