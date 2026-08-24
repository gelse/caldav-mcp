"""Unit tests for the server-timezone parsing policy in _parse_dt.

These tests verify that naive (date-only) inputs are interpreted in the
server timezone (SERVER_TZ) rather than UTC, while explicitly-offset
inputs keep their original offset.
"""

from datetime import UTC, datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from caldav_mcp.datetime_utils import _parse_dt

# Fixed server timezone for deterministic naive-input expectations.
SERVER_TZ = ZoneInfo("Europe/Vienna")


def _expect(got, expected):
    assert got.tzinfo is not None, "result must be timezone-aware"
    assert got == expected


def test_date_only_uses_server_timezone():
    with mock.patch("caldav_mcp.datetime_utils.SERVER_TZ", ZoneInfo("Europe/Vienna")):
        dt = _parse_dt("2026-08-17")
    assert dt.tzinfo == ZoneInfo("Europe/Vienna")
    assert dt.strftime("%H:%M:%S") == "00:00:00"


def test_date_only_utc_fallback():
    with mock.patch("caldav_mcp.datetime_utils.SERVER_TZ", UTC):
        dt = _parse_dt("2026-08-17")
    assert dt.tzinfo == UTC
    assert dt.strftime("%H:%M:%S") == "00:00:00"


def test_explicit_z_offset_unchanged():
    with mock.patch("caldav_mcp.datetime_utils.SERVER_TZ", ZoneInfo("Europe/Vienna")):
        dt = _parse_dt("2026-08-17T10:00:00Z")
    assert dt.tzinfo == UTC
    assert dt.strftime("%H:%M:%S") == "10:00:00"


def test_explicit_plus_offset_unchanged():
    with mock.patch("caldav_mcp.datetime_utils.SERVER_TZ", ZoneInfo("Europe/Vienna")):
        dt = _parse_dt("2026-08-17T10:00:00+05:30")
    assert dt.tzinfo == timezone(timedelta(hours=5, minutes=30))
    assert dt.strftime("%H:%M:%S") == "10:00:00"


@pytest.fixture(autouse=True)
def _vienna_server_tz():
    """Pin the server timezone to Vienna for the naive-input tests below."""
    patcher = mock.patch("caldav_mcp.datetime_utils.SERVER_TZ", SERVER_TZ)
    patcher.start()
    yield
    patcher.stop()


def test_full_seconds_with_offset():
    expected = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone(timedelta(hours=1)))
    _expect(_parse_dt("2026-01-01T10:00:00+0100"), expected)


def test_minutes_with_offset():
    expected = datetime(2026, 1, 1, 10, 30, tzinfo=timezone(timedelta(hours=1)))
    _expect(_parse_dt("2026-01-01T10:30+0100"), expected)


def test_space_separator_seconds_with_offset():
    expected = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone(timedelta(hours=1)))
    _expect(_parse_dt("2026-01-01 10:00:00+0100"), expected)


def test_space_separator_minutes_with_offset():
    expected = datetime(2026, 1, 1, 10, 30, tzinfo=timezone(timedelta(hours=1)))
    _expect(_parse_dt("2026-01-01 10:30+0100"), expected)


def test_z_suffix_normalized_to_utc():
    expected = datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC)
    _expect(_parse_dt("2026-01-01T10:00:00Z"), expected)


def test_naive_datetime_full_seconds_uses_server_tz():
    expected = datetime(2026, 1, 1, 10, 0, 0, tzinfo=SERVER_TZ)
    _expect(_parse_dt("2026-01-01T10:00:00"), expected)


def test_naive_datetime_minutes_uses_server_tz():
    expected = datetime(2026, 1, 1, 10, 30, tzinfo=SERVER_TZ)
    _expect(_parse_dt("2026-01-01T10:30"), expected)


def test_naive_space_separator_full_seconds_uses_server_tz():
    expected = datetime(2026, 1, 1, 10, 0, 0, tzinfo=SERVER_TZ)
    _expect(_parse_dt("2026-01-01 10:00:00"), expected)


def test_naive_space_separator_minutes_uses_server_tz():
    expected = datetime(2026, 1, 1, 10, 30, tzinfo=SERVER_TZ)
    _expect(_parse_dt("2026-01-01 10:30"), expected)


def test_date_only_uses_server_tz():
    expected = datetime(2026, 1, 1, 0, 0, 0, tzinfo=SERVER_TZ)
    _expect(_parse_dt("2026-01-01"), expected)


def test_whitespace_trimmed():
    expected = datetime(2026, 1, 1, 10, 0, 0, tzinfo=SERVER_TZ)
    _expect(_parse_dt("  2026-01-01T10:00:00  "), expected)


def test_empty_string_defaults_to_now():
    now = datetime(2026, 6, 5, 12, 30, 45, tzinfo=SERVER_TZ)
    with mock.patch("caldav_mcp.datetime_utils._now", return_value=now):
        got = _parse_dt("")
    assert got == now


def test_whitespace_only_defaults_to_now():
    now = datetime(2026, 6, 5, 12, 30, 45, tzinfo=SERVER_TZ)
    with mock.patch("caldav_mcp.datetime_utils._now", return_value=now):
        got = _parse_dt("   ")
    assert got == now


def test_invalid_input_raises_value_error():
    with pytest.raises(ValueError):
        _parse_dt("not-a-date")
