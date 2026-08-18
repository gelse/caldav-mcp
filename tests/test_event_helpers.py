"""Unit tests for the pure serialization helpers in server.py.

Covers ``_comp``, ``_event_to_dict`` and ``_attendee_str``. These are pure
functions over ``icalendar`` components, so no CalDAV network mocking is
needed -- we build real ``icalendar.Event`` / ``icalendar.vCalAddress``
objects and pass lightweight wrappers that expose ``icalendar_component``.
"""

import unittest
from datetime import datetime

from icalendar import Calendar, Event, vCalAddress

import server


def make_event_wrapper(component):
    """Wrap an icalendar component so server._comp can find it."""

    class Wrapper:
        def __init__(self, comp):
            self.icalendar_component = comp

    return Wrapper(component)


class CompTest(unittest.TestCase):
    def test_comp_returns_attached_component(self):
        comp = Event()
        wrapper = make_event_wrapper(comp)
        self.assertIs(server._comp(wrapper), comp)

    def test_comp_returns_none_without_component(self):
        class Bare:
            pass

        self.assertIsNone(server._comp(Bare()))


class EventToDictTest(unittest.TestCase):
    def _build_event(self):
        event = Event()
        event.add("uid", "event-123")
        event.add("summary", "Team standup")
        event.add("dtstart", datetime(2026, 8, 18, 9, 0, 0))
        event.add("dtend", datetime(2026, 8, 18, 9, 30, 0))
        event.add("location", "Room 1")
        event.add("description", "Sync on the roadmap")
        return event

    def test_event_to_dict_maps_all_fields(self):
        cal = Calendar()
        cal.add_component(self._build_event())
        wrapper = make_event_wrapper(cal.walk("VEVENT")[0])

        result = server._event_to_dict(wrapper)

        self.assertIsInstance(result, dict)
        self.assertEqual(
            list(result.keys()),
            ["uid", "summary", "dtstart", "dtend", "location", "description",
             "categories", "attendees"],
        )
        self.assertEqual(result["uid"], "event-123")
        self.assertEqual(result["summary"], "Team standup")
        self.assertEqual(result["location"], "Room 1")
        self.assertEqual(result["description"], "Sync on the roadmap")
        self.assertEqual(result["attendees"], "")
        self.assertEqual(result["categories"], "")

    def test_event_to_dict_dtstart_isoformat(self):
        cal = Calendar()
        cal.add_component(self._build_event())
        wrapper = make_event_wrapper(cal.walk("VEVENT")[0])

        result = server._event_to_dict(wrapper)
        # Naive datetimes are serialized via isoformat, e.g. 2026-08-18T09:00:00
        self.assertEqual(result["dtstart"], "2026-08-18T09:00:00")
        self.assertEqual(result["dtend"], "2026-08-18T09:30:00")

    def test_event_to_dict_with_attendees(self):
        event = self._build_event()
        event.add("attendee", vCalAddress("mailto:alice@example.com"))
        cal = Calendar()
        cal.add_component(event)
        wrapper = make_event_wrapper(cal.walk("VEVENT")[0])

        result = server._event_to_dict(wrapper)
        self.assertEqual(result["attendees"], "mailto:alice@example.com")

    def test_event_to_dict_multiple_attendees_semicolon_joined(self):
        event = self._build_event()
        event.add("attendee", vCalAddress("mailto:alice@example.com"))
        event.add("attendee", vCalAddress("mailto:bob@example.com"))
        cal = Calendar()
        cal.add_component(event)
        wrapper = make_event_wrapper(cal.walk("VEVENT")[0])

        result = server._event_to_dict(wrapper)
        self.assertEqual(
            result["attendees"],
            "mailto:alice@example.com; mailto:bob@example.com",
        )

    def test_event_to_dict_missing_component_uses_event_id(self):
        class BareEvent:
            id = "fallback-id"

        result = server._event_to_dict(BareEvent())
        self.assertEqual(result["uid"], "fallback-id")
        self.assertEqual(result["summary"], "")
        self.assertEqual(result["attendees"], "")


class AttendeeStrTest(unittest.TestCase):
    def test_attendee_str_with_roled_partstat(self):
        attendee = vCalAddress("mailto:alice@example.com")
        attendee.params["ROLE"] = "CHAIR"
        attendee.params["PARTSTAT"] = "ACCEPTED"

        self.assertEqual(
            server._attendee_str(attendee),
            "mailto:alice@example.com ROLE=CHAIR PARTSTAT=ACCEPTED",
        )

    def test_attendee_str_with_role_only(self):
        attendee = vCalAddress("mailto:bob@example.com")
        attendee.params["ROLE"] = "REQ-PARTICIPANT"

        self.assertEqual(
            server._attendee_str(attendee),
            "mailto:bob@example.com ROLE=REQ-PARTICIPANT",
        )

    def test_attendee_str_no_params(self):
        attendee = vCalAddress("mailto:carol@example.com")

        self.assertEqual(server._attendee_str(attendee), "mailto:carol@example.com")


if __name__ == "__main__":
    unittest.main()
