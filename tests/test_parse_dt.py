"""Unit tests for the server-timezone parsing policy in _parse_dt.

These tests verify that naive (date-only) inputs are interpreted in the
server timezone (SERVER_TZ) rather than UTC, while explicitly-offset
inputs keep their original offset.
"""

import unittest
from datetime import timedelta, timezone
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


if __name__ == "__main__":
    unittest.main()
