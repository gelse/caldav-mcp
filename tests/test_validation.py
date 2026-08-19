"""Validation tests for priority and rrule helpers plus end-to-end rejection.

This module (from plan 11c) targets the extracted helpers ``_validate_priority``
(plan 11a) and ``_validate_rrule`` (plan 11b), and additionally confirms that
``caldav_create_event`` rejects invalid input with clear ``ERROR:`` strings.
The end-to-end tests reuse the fake-network pattern from
``tests/test_create_event.py`` so everything stays network-free and deterministic.
"""

import unittest
from unittest import mock

import server
from server import Status


class ValidatePriorityTest(unittest.TestCase):
    """Behavior of ``_validate_priority`` (returns ``(int|None, str|None)``)."""

    def test_empty_returns_none(self):
        self.assertEqual(server._validate_priority(""), (None, None))

    def test_valid(self):
        self.assertEqual(server._validate_priority("0"), (0, None))
        self.assertEqual(server._validate_priority("9"), (9, None))

    def test_non_integer(self):
        _, err = server._validate_priority("abc")
        self.assertEqual(err, "priority must be an integer")

    def test_out_of_range(self):
        _, err = server._validate_priority("10")
        self.assertEqual(err, "priority must be between 0 and 9")
        _, err = server._validate_priority("-1")
        self.assertEqual(err, "priority must be between 0 and 9")


class ValidateRruleTest(unittest.TestCase):
    """Behavior of ``_validate_rrule`` (returns bool, True = empty or valid)."""

    def test_empty_is_true(self):
        self.assertTrue(server._validate_rrule(""))

    def test_valid_daily(self):
        self.assertTrue(server._validate_rrule("FREQ=DAILY"))

    def test_invalid(self):
        self.assertFalse(server._validate_rrule("NOT-A-RRULE;;"))


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


class CreateEventValidationTest(unittest.TestCase):
    """End-to-end rejection and success through ``caldav_create_event``."""

    def setUp(self):
        self.fake_cal = FakeCalendar()
        self.patchers = patch_network(self.fake_cal)
        for p in self.patchers:
            p.start()
        self.addCleanup(self._stop_patchers)

    def _stop_patchers(self):
        for p in self.patchers:
            p.stop()

    def test_invalid_priority_non_integer_returns_error(self):
        result = server.caldav_create_event(
            summary="s", start="2026-01-01T10:00:00Z", priority="abc"
        )
        self.assertEqual(result.status, Status.ERROR)
        self.assertIn("priority must be an integer", result.message)
        self.assertIsNone(self.fake_cal.saved)

    def test_invalid_priority_out_of_range_returns_error(self):
        result = server.caldav_create_event(
            summary="s", start="2026-01-01T10:00:00Z", priority="10"
        )
        self.assertEqual(result.status, Status.ERROR)
        self.assertIn("priority must be between 0 and 9", result.message)
        self.assertIsNone(self.fake_cal.saved)

    def test_invalid_rrule_returns_error(self):
        result = server.caldav_create_event(
            summary="s", start="2026-01-01T10:00:00Z", rrule="NOT-A-RRULE;;"
        )
        self.assertEqual(result.status, Status.ERROR)
        self.assertIn("invalid RRULE", result.message)
        self.assertIsNone(self.fake_cal.saved)

    def test_valid_priority_and_rrule_succeed(self):
        result = server.caldav_create_event(
            summary="s",
            start="2026-01-01T10:00:00Z",
            priority="5",
            rrule="FREQ=DAILY;COUNT=5",
        )
        self.assertEqual(result.status, Status.OK, msg=f"call failed: {result!r}")
        self.assertIsNotNone(self.fake_cal.saved)

    def test_empty_priority_and_empty_rrule_succeed(self):
        result = server.caldav_create_event(
            summary="s", start="2026-01-01T10:00:00Z"
        )
        self.assertEqual(result.status, Status.OK, msg=f"call failed: {result!r}")
        self.assertIsNotNone(self.fake_cal.saved)


if __name__ == "__main__":
    unittest.main()
