"""Unit tests for the default start/end boundaries of caldav_get_events.

Sub-plan 02c: the default start boundary must be the server-timezone local
midnight (`_start_of_day(_now())`), and the default end must be exactly 24
hours later. These tests exercise that exact logic combination.

Note: `caldav_get_events` itself requires live CalDAV credentials and a
network round-trip, so it is not unit-tested here. Instead we verify the
helper logic that produces the default boundary values.
"""

from datetime import UTC, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import server


def test_default_start_is_server_local_midnight():
    # Mid-afternoon instant in Europe/Vienna (UTC+2 in summer).
    tz = ZoneInfo("Europe/Vienna")
    fake_now = datetime(2026, 8, 18, 15, 30, 45, tzinfo=tz)
    with mock.patch.object(server, "SERVER_TZ", tz):
        with mock.patch.object(server, "_now", lambda: fake_now):
            start_dt = server._start_of_day(server._now())
            end_dt = start_dt + timedelta(days=1)

    # Default start is local midnight with the server offset (+02:00).
    assert start_dt.isoformat() == "2026-08-18T00:00:00+02:00"
    assert start_dt.hour == 0
    assert start_dt.minute == 0
    assert start_dt.second == 0
    assert start_dt.microsecond == 0
    assert start_dt.utcoffset() == timedelta(hours=2)
    # Default end is exactly 24 hours later, in the same timezone.
    assert end_dt == start_dt + timedelta(days=1)
    assert end_dt.isoformat() == "2026-08-19T00:00:00+02:00"


def test_default_start_uses_utc_midnight_when_server_is_utc():
    tz_utc = UTC
    fake_now = datetime(2026, 8, 18, 15, 30, 45, tzinfo=tz_utc)
    with mock.patch.object(server, "SERVER_TZ", tz_utc):
        with mock.patch.object(server, "_now", lambda: fake_now):
            start_dt = server._start_of_day(server._now())
            end_dt = start_dt + timedelta(days=1)

    assert start_dt.isoformat() == "2026-08-18T00:00:00+00:00"
    assert end_dt == start_dt + timedelta(days=1)
