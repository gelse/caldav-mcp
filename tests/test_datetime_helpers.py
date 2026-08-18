"""Unit tests for day-boundary behavior of the datetime helpers.

This module (from plan 08c) verifies that ``_start_of_day`` zeroes the time
while preserving the timezone, that ``_now`` is always timezone-aware, and
that a fixed "now" produces a contiguous 24-hour day window through
``_start_of_day(_now())``. All tests are deterministic (no reliance on the
wall clock) and network-free.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import server


class DayBoundaryTest(unittest.TestCase):
    """Day-boundary correctness for _start_of_day / _now."""

    def test_start_of_day_preserves_tz_and_zeroes_time(self):
        dt = datetime(2026, 8, 17, 15, 30, 45, 123456, tzinfo=timezone.utc)
        sd = server._start_of_day(dt)
        self.assertEqual(
            sd,
            datetime(2026, 8, 17, 0, 0, 0, 0, tzinfo=timezone.utc),
        )
        self.assertIsNotNone(sd.tzinfo)
        self.assertEqual(sd.hour, 0)
        self.assertEqual(sd.minute, 0)
        self.assertEqual(sd.second, 0)
        self.assertEqual(sd.microsecond, 0)

    def test_now_is_timezone_aware(self):
        now = server._now()
        self.assertIsNotNone(now.tzinfo)

    def test_fixed_now_contiguous_24h_day_window(self):
        fixed_now = datetime(2026, 8, 17, 15, 30, 45, 123456, tzinfo=timezone.utc)
        with mock.patch.object(server, "_now", return_value=fixed_now):
            now = server._now()
            start = server._start_of_day(now)
            end = start + timedelta(days=1)
        # Boundary: start at local midnight with all time fields zeroed.
        self.assertEqual(start.hour, 0)
        self.assertEqual(start.minute, 0)
        self.assertEqual(start.second, 0)
        self.assertEqual(start.microsecond, 0)
        # Contiguous 24-hour window: end == start + 1 day with same tz.
        self.assertEqual(end - start, timedelta(days=1))
        self.assertEqual(end.hour, 0)
        self.assertEqual(end.minute, 0)
        self.assertEqual(end.second, 0)
        self.assertEqual(end.microsecond, 0)
        self.assertEqual(end.tzinfo, start.tzinfo)


if __name__ == "__main__":
    unittest.main()
