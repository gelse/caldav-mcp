"""Unit tests for the RFC 5545 escaping fix in caldav_create_event.

These tests mock the CalDAV network boundaries so we can capture and
round-trip the serialized iCal payload without a live server.
"""

import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from icalendar import Calendar

import server


class FakeCalendar:
    """Minimal stand-in for a caldav Calendar object that records saved payloads."""

    def __init__(self, name=""):
        self.name = name
        self.saved = None

    def save_event(self, data):
        self.saved = data


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


class CreateEventEscapingTest(unittest.TestCase):
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
        result = server.caldav_create_event(**kwargs)
        self.assertTrue(result.startswith("OK:"), msg=f"call failed: {result!r}")
        saved = self.fake_cal.saved
        if saved is None:
            self.fail("no payload was saved")
        return Calendar.from_ical(saved)

    def _event(self, parsed):
        """Return the single VEVENT subcomponent of a parsed calendar."""
        events = parsed.walk("VEVENT")
        self.assertEqual(len(events), 1)
        return events[0]

    def test_summary_with_special_characters_round_trips(self):
        summary = "a,b;c\\d\ne"
        parsed = self._create(summary=summary, start="2026-01-01T10:00:00Z")
        self.assertEqual(str(self._event(parsed)["summary"]), summary)

    def test_location_and_description_special_characters(self):
        location = "Room 1, Building A"
        description = "line1\nline2"
        parsed = self._create(
            summary="s",
            start="2026-01-01T10:00:00Z",
            location=location,
            description=description,
        )
        ev = self._event(parsed)
        self.assertEqual(str(ev["location"]), location)
        self.assertEqual(str(ev["description"]), description)

    def test_multiple_attendees(self):
        parsed = self._create(
            summary="s",
            start="2026-01-01T10:00:00Z",
            attendees="a@example.com, b@example.com",
        )
        ev = self._event(parsed)
        attendees = ev.get("attendee")
        if not isinstance(attendees, (list, tuple)):
            attendees = [attendees]
        self.assertEqual(len(attendees), 2)
        emails = sorted(str(a) for a in attendees)
        self.assertEqual(emails, ["mailto:a@example.com", "mailto:b@example.com"])
        for a in attendees:
            params = a.params
            self.assertEqual(params.get("PARTSTAT"), "NEEDS-ACTION")
            self.assertEqual(params.get("RSVP"), "TRUE")

    def test_emoji_in_summary(self):
        summary = "🎉 party"
        parsed = self._create(summary=summary, start="2026-01-01T10:00:00Z")
        self.assertEqual(str(self._event(parsed)["summary"]), summary)

    def test_empty_optional_fields(self):
        parsed = self._create(summary="s", start="2026-01-01T10:00:00Z")
        ev = self._event(parsed)
        self.assertIn("uid", ev)
        self.assertIn("dtstart", ev)
        self.assertIn("dtend", ev)
        self.assertIn("dtstamp", ev)
        self.assertEqual(ev.get("summary"), "s")
        self.assertNotIn("location", ev)
        self.assertNotIn("description", ev)
        self.assertNotIn("categories", ev)
        self.assertNotIn("priority", ev)
        self.assertNotIn("rrule", ev)
        self.assertNotIn("attendee", ev)

    def test_valid_priority(self):
        parsed = self._create(summary="s", start="2026-01-01T10:00:00Z", priority="5")
        self.assertEqual(int(self._event(parsed)["priority"]), 5)

    def test_invalid_priority_non_integer(self):
        result = server.caldav_create_event(
            summary="s", start="2026-01-01T10:00:00Z", priority="high"
        )
        self.assertIn("priority must be an integer", result)

    def test_invalid_priority_out_of_range(self):
        result = server.caldav_create_event(
            summary="s", start="2026-01-01T10:00:00Z", priority="10"
        )
        self.assertIn("priority must be between 0 and 9", result)

    def test_invalid_rrule(self):
        result = server.caldav_create_event(
            summary="s", start="2026-01-01T10:00:00Z", rrule="FREQ=BOGUS"
        )
        self.assertIn("invalid RRULE", result)

    def test_valid_rrule(self):
        parsed = self._create(summary="s", start="2026-01-01T10:00:00Z", rrule="FREQ=DAILY;COUNT=5")
        self.assertIn("rrule", self._event(parsed))

    def test_dtstamp_uses_timezone_aware_now(self):
        """DTSTAMP must derive from the tz-aware _now() in the server timezone.

        Previously this used a hardcoded datetime.now(timezone.utc). After the
        fix the value comes from _now(), which carries SERVER_TZ, and when
        serialized to iCal it must match the corresponding UTC instant.
        """
        vienna = ZoneInfo("Europe/Vienna")
        fake_now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=vienna)
        with mock.patch.object(server, "SERVER_TZ", vienna):
            with mock.patch.object(server, "_now", lambda: fake_now):
                result = server.caldav_create_event(
                    summary="s", start="2026-01-01T10:00:00Z"
                )
        self.assertTrue(result.startswith("OK:"), msg=f"call failed: {result!r}")
        payload = self.fake_cal.saved
        if payload is None:
            self.fail("no payload was saved")
        # 10:00 in Europe/Vienna (UTC+1 in January) == 09:00Z.
        self.assertIn("DTSTAMP:20260101T090000Z", payload)


if __name__ == "__main__":
    unittest.main()
