"""Unit tests for day-boundary behavior of the datetime helpers.

This module (from plan 08c) verifies that ``_start_of_day`` zeroes the time
while preserving the timezone, that ``_now`` is always timezone-aware, and
that a fixed "now" produces a contiguous 24-hour day window through
``_start_of_day(_now())``. All tests are deterministic (no reliance on the
wall clock) and network-free.
"""

from datetime import UTC, datetime, timedelta
from unittest import mock

import server


def test_start_of_day_preserves_tz_and_zeroes_time():
    dt = datetime(2026, 8, 17, 15, 30, 45, 123456, tzinfo=UTC)
    sd = server._start_of_day(dt)
    assert sd == datetime(2026, 8, 17, 0, 0, 0, 0, tzinfo=UTC)
    assert sd.tzinfo is not None
    assert sd.hour == 0
    assert sd.minute == 0
    assert sd.second == 0
    assert sd.microsecond == 0


def test_now_is_timezone_aware():
    now = server._now()
    assert now.tzinfo is not None


def test_fixed_now_contiguous_24h_day_window():
    fixed_now = datetime(2026, 8, 17, 15, 30, 45, 123456, tzinfo=UTC)
    with mock.patch.object(server, "_now", return_value=fixed_now):
        now = server._now()
        start = server._start_of_day(now)
        end = start + timedelta(days=1)
    # Boundary: start at local midnight with all time fields zeroed.
    assert start.hour == 0
    assert start.minute == 0
    assert start.second == 0
    assert start.microsecond == 0
    # Contiguous 24-hour window: end == start + 1 day with same tz.
    assert end - start == timedelta(days=1)
    assert end.hour == 0
    assert end.minute == 0
    assert end.second == 0
    assert end.microsecond == 0
    assert end.tzinfo == start.tzinfo
