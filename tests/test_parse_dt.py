"""Unit tests for the server-timezone parsing policy in _parse_dt.

These tests verify that naive (date-only) inputs are interpreted in the
server timezone (SERVER_TZ) rather than UTC, while explicitly-offset
inputs keep their original offset.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from zoneinfo import ZoneInfo

import server


class ParseDtServerTimezoneTest(unittest.TestCase):
    def test_date_only_uses_server_timezone(self):
        with mock.patch.object(server, "SERVER_TZ", ZoneInfo("Europe/Vienna")):
            dt = server._parse_dt("2026-08-17")
        self.assertEqual(dt.tzinfo, ZoneInfo("Europe/Vienna"))
        self.assertEqual(dt.strftime("%H:%M:%S"), "00:00:00")

    def test_date_only_utc_fallback(self):
        with mock.patch.object(server, "SERVER_TZ", timezone.utc):
            dt = server._parse_dt("2026-08-17")
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.strftime("%H:%M:%S"), "00:00:00")

    def test_explicit_z_offset_unchanged(self):
        with mock.patch.object(server, "SERVER_TZ", ZoneInfo("Europe/Vienna")):
            dt = server._parse_dt("2026-08-17T10:00:00Z")
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.strftime("%H:%M:%S"), "10:00:00")

    def test_explicit_plus_offset_unchanged(self):
        with mock.patch.object(server, "SERVER_TZ", ZoneInfo("Europe/Vienna")):
            dt = server._parse_dt("2026-08-17T10:00:00+05:30")
        self.assertEqual(dt.tzinfo, timezone(timedelta(hours=5, minutes=30)))
        self.assertEqual(dt.strftime("%H:%M:%S"), "10:00:00")


class ParseDtTest(unittest.TestCase):
    """Comprehensive coverage of every accepted _parse_dt format and edge case."""

    # Fixed server timezone for deterministic naive-input expectations.
    SERVER_TZ = ZoneInfo("Europe/Vienna")

    def setUp(self):
        self.patcher = mock.patch.object(server, "SERVER_TZ", self.SERVER_TZ)
        self.patcher.start()
        super().setUp()

    def tearDown(self):
        self.patcher.stop()
        super().tearDown()

    def _expect(self, got, expected):
        self.assertIsNotNone(got.tzinfo, "result must be timezone-aware")
        self.assertEqual(got, expected)

    def test_full_seconds_with_offset(self):
        expected = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone(timedelta(hours=1)))
        self._expect(server._parse_dt("2026-01-01T10:00:00+0100"), expected)

    def test_minutes_with_offset(self):
        expected = datetime(2026, 1, 1, 10, 30, tzinfo=timezone(timedelta(hours=1)))
        self._expect(server._parse_dt("2026-01-01T10:30+0100"), expected)

    def test_space_separator_seconds_with_offset(self):
        expected = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone(timedelta(hours=1)))
        self._expect(server._parse_dt("2026-01-01 10:00:00+0100"), expected)

    def test_space_separator_minutes_with_offset(self):
        expected = datetime(2026, 1, 1, 10, 30, tzinfo=timezone(timedelta(hours=1)))
        self._expect(server._parse_dt("2026-01-01 10:30+0100"), expected)

    def test_z_suffix_normalized_to_utc(self):
        expected = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        self._expect(server._parse_dt("2026-01-01T10:00:00Z"), expected)

    def test_naive_datetime_full_seconds_uses_server_tz(self):
        expected = datetime(2026, 1, 1, 10, 0, 0, tzinfo=self.SERVER_TZ)
        self._expect(server._parse_dt("2026-01-01T10:00:00"), expected)

    def test_naive_datetime_minutes_uses_server_tz(self):
        expected = datetime(2026, 1, 1, 10, 30, tzinfo=self.SERVER_TZ)
        self._expect(server._parse_dt("2026-01-01T10:30"), expected)

    def test_naive_space_separator_full_seconds_uses_server_tz(self):
        expected = datetime(2026, 1, 1, 10, 0, 0, tzinfo=self.SERVER_TZ)
        self._expect(server._parse_dt("2026-01-01 10:00:00"), expected)

    def test_naive_space_separator_minutes_uses_server_tz(self):
        expected = datetime(2026, 1, 1, 10, 30, tzinfo=self.SERVER_TZ)
        self._expect(server._parse_dt("2026-01-01 10:30"), expected)

    def test_date_only_uses_server_tz(self):
        expected = datetime(2026, 1, 1, 0, 0, 0, tzinfo=self.SERVER_TZ)
        self._expect(server._parse_dt("2026-01-01"), expected)

    def test_whitespace_trimmed(self):
        expected = datetime(2026, 1, 1, 10, 0, 0, tzinfo=self.SERVER_TZ)
        self._expect(server._parse_dt("  2026-01-01T10:00:00  "), expected)

    def test_empty_string_defaults_to_now(self):
        now = datetime(2026, 6, 5, 12, 30, 45, tzinfo=self.SERVER_TZ)
        with mock.patch.object(server, "_now", return_value=now):
            got = server._parse_dt("")
        self.assertEqual(got, now)

    def test_whitespace_only_defaults_to_now(self):
        now = datetime(2026, 6, 5, 12, 30, 45, tzinfo=self.SERVER_TZ)
        with mock.patch.object(server, "_now", return_value=now):
            got = server._parse_dt("   ")
        self.assertEqual(got, now)

    def test_invalid_input_raises_value_error(self):
        with self.assertRaises(ValueError):
            server._parse_dt("not-a-date")


if __name__ == "__main__":
    unittest.main()
