"""Round-trip (serialize -> re-parse -> read) tests for caldav_create_event.

These tests verify that a fully-populated event (summary, dtstart/dtend,
location, description, categories, attendees, priority, rrule) survives a
complete round trip:

    caldav_create_event -> cal.save_event(ical.to_ical()) -> from_ical re-parse

and that the resulting component can be read back through the same helpers
used by ``caldav_get_event_by_uid`` (``_event_to_dict``). The CalDAV network
boundary is mocked with in-memory fakes, mirroring ``test_create_event.py``.
"""

import unittest
from unittest import mock

from icalendar import Calendar

import server


class FakeObject:
    """Generic stand-in carrying an ``icalendar_component`` attribute.

    ``caldav_get_event_by_uid`` reads ``event.id`` (uid) and relies on
    ``_comp(event)`` returning the ``icalendar_component`` attribute, so a
    plain holder wrapped around a real ``Event`` is all the read path needs.
    """

    def __init__(self, comp):
        self.icalendar_component = comp
        self.id = str(comp.get("uid"))


class FakeCalendar:
    """Minimal stand-in for a caldav Calendar that stores saved payloads."""

    def __init__(self, name=""):
        self.name = name
        self.saved = []

    def save_event(self, data):
        self.saved.append(data)

    def last_event(self):
        """Return a FakeObject wrapping the VEVENT of the last saved payload."""
        parsed = Calendar.from_ical(self.saved[-1])
        events = parsed.walk("VEVENT")
        return FakeObject(events[0])


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
    """Patch the CalDAV boundaries so create_event uses a fake calendar."""
    return [
        mock.patch.object(server, "_resolve_credentials", return_value=("u", "p", "w")),
        mock.patch.object(server, "DAVClient", return_value=FakeClient([fake_cal])),
        mock.patch.object(server, "_get_calendar", return_value=fake_cal),
    ]


class EventRoundtripTest(unittest.TestCase):
    def setUp(self):
        self.fake_cal = FakeCalendar()
        self.patchers = patch_network(self.fake_cal)
        for p in self.patchers:
            p.start()
        self.addCleanup(self._stop_patchers)

    def _stop_patchers(self):
        for p in self.patchers:
            p.stop()

    def _create(self, **kwargs):
        """Create an event and return the re-parsed VEVENT component."""
        summary = kwargs.get("summary", "s")
        result = server.caldav_create_event(**kwargs)
        self.assertTrue(result.startswith(f"OK: Event '{summary}'"), msg=result)
        return self.fake_cal.last_event()

    def _event_from_payload(self, index=-1):
        parsed = Calendar.from_ical(self.fake_cal.saved[index])
        events = parsed.walk("VEVENT")
        self.assertEqual(len(events), 1)
        return events[0]

    def test_full_event_round_trips_all_fields(self):
        """A fully-populated event survives serialize -> re-parse -> read."""
        summary = "Back\\slash, comma; and\nnewline"
        location = "Room 3, Building B; Floor 2"
        description = "line one\nline two"
        categories = "Work, Important"
        attendees = "alice@example.com, bob@example.com"
        start = "2026-06-01T10:00:00Z"
        end = "2026-06-01T11:30:00Z"

        ev = self._create(
            summary=summary,
            start=start,
            end=end,
            location=location,
            description=description,
            categories=categories,
            priority="5",
            rrule="FREQ=DAILY;COUNT=5",
            attendees=attendees,
        )

        # Re-parse the raw saved payload once more to confirm full fidelity.
        raw = self._event_from_payload()
        self.assertEqual(str(raw["summary"]), summary)
        self.assertEqual(str(raw["location"]), location)
        self.assertEqual(str(raw["description"]), description)
        # CATEGORIES is serialised as a single value with escaped separators
        # ("Work\, Important") and decodes back to the plain comma-joined string.
        self.assertEqual(raw.decoded("categories"), [categories])
        self.assertEqual(int(raw["priority"]), 5)
        self.assertIn("rrule", raw)
        self.assertIn("dtstart", raw)
        self.assertIn("dtend", raw)

        # The read path (_event_to_dict) used by caldav_get_event_by_uid
        # reflects the same text values after the re-parse.
        d = server._event_to_dict(ev)
        self.assertEqual(d["summary"], summary)
        self.assertEqual(d["location"], location)
        self.assertEqual(d["description"], description)
        self.assertEqual(d["uid"], str(raw["uid"]))

    def test_summary_with_special_characters_round_trips(self):
        summary = "a,b;c\\d\ne"
        ev = self._create(summary=summary, start="2026-01-01T10:00:00Z")
        self.assertEqual(str(ev.icalendar_component["summary"]), summary)
        self.assertEqual(server._event_to_dict(ev)["summary"], summary)

    def test_dtstart_dtend_round_trip_utc(self):
        ev = self._create(
            summary="s",
            start="2026-03-05T09:00:00Z",
            end="2026-03-05T10:30:00Z",
        )
        d = server._event_to_dict(ev)
        self.assertEqual(d["dtstart"], "2026-03-05T09:00:00+00:00")
        self.assertEqual(d["dtend"], "2026-03-05T10:30:00+00:00")

    def test_attendees_round_trip(self):
        self._create(
            summary="s",
            start="2026-01-01T10:00:00Z",
            attendees="alice@example.com, bob@example.com",
        )
        parsed_ev = self._event_from_payload()
        attendees = parsed_ev.get("attendee")
        if not isinstance(attendees, (list, tuple)):
            attendees = [attendees]
        emails = sorted(str(a) for a in attendees)
        self.assertEqual(emails, ["mailto:alice@example.com", "mailto:bob@example.com"])
        for a in attendees:
            self.assertEqual(a.params.get("PARTSTAT"), "NEEDS-ACTION")
            self.assertEqual(a.params.get("RSVP"), "TRUE")
            self.assertEqual(a.params.get("ROLE"), "REQ-PARTICIPANT")

    def test_priority_round_trip(self):
        self._create(
            summary="s",
            start="2026-01-01T10:00:00Z",
            priority="7",
        )
        self.assertEqual(int(self._event_from_payload()["priority"]), 7)

    def test_rrule_round_trip(self):
        self._create(
            summary="s",
            start="2026-01-01T10:00:00Z",
            end="2026-01-01T11:00:00Z",
            rrule="FREQ=WEEKLY;BYDAY=MO;COUNT=10",
        )
        self.assertIn("rrule", self._event_from_payload())

    def test_empty_optional_fields_absent_after_roundtrip(self):
        self._create(summary="s", start="2026-01-01T10:00:00Z")
        raw = self._event_from_payload()
        self.assertIn("uid", raw)
        self.assertIn("dtstart", raw)
        self.assertIn("dtend", raw)
        self.assertIn("dtstamp", raw)
        self.assertNotIn("location", raw)
        self.assertNotIn("description", raw)
        self.assertNotIn("categories", raw)
        self.assertNotIn("priority", raw)
        self.assertNotIn("rrule", raw)
        self.assertNotIn("attendee", raw)


if __name__ == "__main__":
    unittest.main()
