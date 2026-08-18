"""Unit tests for caldav_move_event.

These tests mock the CalDAV network boundaries and provide a fake source event
with a parsed icalendar component plus a fake destination calendar that captures
what is saved, verifying that the move sets a fresh UID on the component and
serializes via comp.to_ical() rather than raw text replacement.
"""

import unittest
from unittest import mock

from icalendar import Calendar, Event

import server


class FakeEvent:
    """Stand-in for a caldav Event whose icalendar_component is the VEVENT."""

    def __init__(self, ical):
        self._ical = ical
        self.data = ical.to_ical().decode("utf-8")
        events = ical.walk("VEVENT")
        self.icalendar_component = events[0]  # _comp reads this attribute
        self.deleted = False

    def delete(self):
        self.deleted = True


class FakeCalendar:
    """Stand-in for a caldav Calendar: returns an event by UID, or saves data."""

    def __init__(self, event=None, name=""):
        self._event = event
        self.name = name
        self.saved = []

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
    def __init__(self):
        pass

    def principal(self):
        return FakePrincipal([])


def make_event():
    """Build a Calendar with one VEVENT and return the parsed ical."""
    cal = Calendar()
    cal.add("prodid", "-//caldav-mcp//EN")
    cal.add("version", "2.0")
    ev = Event()
    ev.add("uid", "move-uid@caldav-mcp")
    ev.add("summary", "Moving")
    cal.add_component(ev)
    return cal


def patch_network(src_cal, dst_cal):
    return [
        mock.patch.object(server, "_resolve_credentials", return_value=("u", "p", "w")),
        mock.patch.object(server, "DAVClient", return_value=FakeClient()),
        mock.patch.object(
            server,
            "_get_calendar",
            side_effect=lambda client, name: dst_cal if name == dst_cal.name else src_cal,
        ),
    ]


class MoveEventComponentTest(unittest.TestCase):
    def setUp(self):
        self.ical = make_event()
        self.event = FakeEvent(self.ical)
        self.src_cal = FakeCalendar(event=self.event, name="src")
        self.dst_cal = FakeCalendar(name="dst")
        self.patchers = patch_network(self.src_cal, self.dst_cal)
        for p in self.patchers:
            p.start()
        self.addCleanup(self._stop_patchers)

    def _stop_patchers(self):
        for p in self.patchers:
            p.stop()

    def test_move_sets_new_uid_on_component(self):
        result = server.caldav_move_event(
            uid="move-uid@caldav-mcp",
            target_calendar="dst",
        )
        self.assertTrue(result.startswith("OK:"), msg=result)
        # The component's UID changed away from the original.
        ev = self.event.icalendar_component
        new_uid = ev.get("uid")
        self.assertNotEqual(new_uid, "move-uid@caldav-mcp")
        self.assertIn("move-uid@caldav-mcp", self.event.data)
        # The destination received a serialized component containing the new UID.
        self.assertEqual(len(self.dst_cal.saved), 1)
        self.assertIn(new_uid, self.dst_cal.saved[0])
        self.assertNotIn("UID:move-uid@caldav-mcp", self.dst_cal.saved[0])
        # Original was deleted.
        self.assertTrue(self.event.deleted)

    def test_move_serializes_via_component_not_text_replacement(self):
        result = server.caldav_move_event(
            uid="move-uid@caldav-mcp",
            target_calendar="dst",
        )
        self.assertTrue(result.startswith("OK:"), msg=result)
        # Saved data must parse back into a VEVENT carrying the new UID.
        parsed = Calendar.from_ical(self.dst_cal.saved[0])
        ev = parsed.walk("VEVENT")[0]
        self.assertNotEqual(ev.get("uid"), "move-uid@caldav-mcp")
        self.assertEqual(ev.get("summary"), "Moving")

    def test_move_returns_new_uid_in_message(self):
        result = server.caldav_move_event(
            uid="move-uid@caldav-mcp",
            target_calendar="dst",
        )
        ev = self.event.icalendar_component
        self.assertIn(f"new uid={ev.get('uid')}", result)

    def test_move_no_component_returns_error(self):
        self.event.icalendar_component = None
        result = server.caldav_move_event(
            uid="move-uid@caldav-mcp",
            target_calendar="dst",
        )
        self.assertIn("no icalendar component", result)
        # Nothing was saved or deleted.
        self.assertEqual(self.dst_cal.saved, [])
        self.assertFalse(self.event.deleted)


if __name__ == "__main__":
    unittest.main()
