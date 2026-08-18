"""Unit tests for caldav_add_attendee using the icalendar component API.

These tests mock the CalDAV network boundaries and provide a fake event with a
parsed icalendar component so we can verify that attendees are appended via the
component API (vCalAddress with ROLE/PARTSTAT/RSVP params) rather than raw text
manipulation.
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

    def save(self):
        # Re-parse the serialized data so icalendar_component reflects new state.
        parsed = Calendar.from_ical(self.data)
        events = parsed.walk("VEVENT")
        self.icalendar_component = events[0]


class FakeCalendar:
    def __init__(self, event):
        self._event = event

    def event_by_uid(self, uid):
        return self._event


class FakePrincipal:
    def __init__(self, calendars):
        self._calendars = calendars

    def calendars(self):
        return self._calendars


class FakeClient:
    def __init__(self, calendars):
        self._calendars = calendars

    def principal(self):
        return FakePrincipal(self._calendars)


def patch_network(fake_cal):
    return [
        mock.patch.object(server, "_resolve_credentials", return_value=("u", "p", "w")),
        mock.patch.object(server, "DAVClient", return_value=FakeClient([fake_cal])),
        mock.patch.object(server, "_get_calendar", return_value=fake_cal),
    ]


def make_event():
    """Build a Calendar with one VEVENT and return the parsed ical."""
    cal = Calendar()
    cal.add("prodid", "-//caldav-mcp//EN")
    cal.add("version", "2.0")
    ev = Event()
    ev.add("uid", "test-uid@caldav-mcp")
    ev.add("summary", "Meeting")
    cal.add_component(ev)
    return cal


class AddAttendeeComponentTest(unittest.TestCase):
    def setUp(self):
        self.ical = make_event()
        self.event = FakeEvent(self.ical)
        self.fake_cal = FakeCalendar(self.event)
        self.patchers = patch_network(self.fake_cal)
        for p in self.patchers:
            p.start()
        self.addCleanup(self._stop_patchers)

    def _stop_patchers(self):
        for p in self.patchers:
            p.stop()

    def _attendees(self):
        ev = self.event.icalendar_component
        attendees = ev.get("attendee")
        if attendees is None:
            return []
        if not isinstance(attendees, (list, tuple)):
            return [attendees]
        return list(attendees)

    def test_adds_attendee_with_component_api(self):
        result = server.caldav_add_attendee(
            uid="test-uid@caldav-mcp",
            email="alice@example.com",
        )
        self.assertTrue(result.startswith("OK:"), msg=result)
        attendees = self._attendees()
        self.assertEqual(len(attendees), 1)
        a = attendees[0]
        self.assertEqual(str(a), "mailto:alice@example.com")
        self.assertEqual(a.params.get("PARTSTAT"), "NEEDS-ACTION")
        self.assertEqual(a.params.get("RSVP"), "TRUE")
        self.assertEqual(a.params.get("ROLE"), "REQ-PARTICIPANT")

    def test_respects_custom_role(self):
        server.caldav_add_attendee(
            uid="test-uid@caldav-mcp",
            email="bob@example.com",
            role="OPT-PARTICIPANT",
        )
        a = self._attendees()[0]
        self.assertEqual(a.params.get("ROLE"), "OPT-PARTICIPANT")

    def test_normalizes_email_with_mailto_prefix(self):
        server.caldav_add_attendee(
            uid="test-uid@caldav-mcp",
            email="mailto:carol@example.com",
        )
        a = self._attendees()[0]
        self.assertEqual(str(a), "mailto:carol@example.com")

    def test_appends_multiple_attendees(self):
        server.caldav_add_attendee(uid="test-uid@caldav-mcp", email="a@example.com")
        server.caldav_add_attendee(uid="test-uid@caldav-mcp", email="b@example.com")
        emails = sorted(str(a) for a in self._attendees())
        self.assertEqual(emails, ["mailto:a@example.com", "mailto:b@example.com"])

    def test_no_component_returns_error(self):
        self.event.icalendar_component = None
        result = server.caldav_add_attendee(
            uid="test-uid@caldav-mcp",
            email="nobody@example.com",
        )
        self.assertIn("no icalendar component", result)

    def test_already_mailto_attendee_not_duplicated(self):
        server.caldav_add_attendee(uid="test-uid@caldav-mcp", email="a@example.com")
        server.caldav_add_attendee(uid="test-uid@caldav-mcp", email="a@example.com")
        self.assertEqual(len(self._attendees()), 2)


if __name__ == "__main__":
    unittest.main()
