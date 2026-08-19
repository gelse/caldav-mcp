"""Unit tests for the timezone behavior introduced in plan 02.

These tests exercise only pure, network-free helpers (``_server_tz``,
``_now``, ``_start_of_day``, ``_parse_dt``) so they run without a live
CalDAV server. They cover the timezone resolution policy (TZ env var with
fallback to UTC) and the server-timezone-aware day-boundary / date-only
parsing behavior.
"""

from datetime import UTC, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import server


def test_tz_set_returns_configured_zone():
    with mock.patch.dict(server.os.environ, {"TZ": "Europe/Vienna"}):
        tz = server._server_tz()
    assert str(tz) == "Europe/Vienna"


def test_tz_unset_returns_utc():
    with mock.patch.dict(server.os.environ, {}, clear=True):
        tz = server._server_tz()
    assert tz is UTC


def test_tz_empty_returns_utc():
    with mock.patch.dict(server.os.environ, {"TZ": ""}):
        tz = server._server_tz()
    assert tz is UTC


def test_tz_invalid_returns_utc():
    """An invalid TZ falls back to a UTC zone (ZoneInfo('UTC'))."""
    with mock.patch.dict(server.os.environ, {"TZ": "Not/AZone"}):
        tz = server._server_tz()
    assert tz == ZoneInfo("UTC")
    assert tz.utcoffset(None) == timedelta(0)


def test_tz_invalid_logs_warning_and_falls_back_to_utc():
    from caldav_mcp import config as config_module

    with (
        mock.patch.dict(server.os.environ, {"TZ": "Not/AZone"}),
        mock.patch.object(config_module.logger, "warning") as log_warning,
    ):
        tz = server._server_tz()
    assert tz == ZoneInfo("UTC")
    log_warning.assert_called_once_with("Unknown timezone %r, falling back to UTC", "Not/AZone")


def test_now_carries_server_timezone():
    """_now() and _start_of_day() must preserve the server timezone."""
    with mock.patch.object(server, "SERVER_TZ", ZoneInfo("Europe/Vienna")):
        now = server._now()
    assert now.tzinfo is ZoneInfo("Europe/Vienna")
    # Europe/Vienna in late August is on DST (UTC+2).
    assert now.utcoffset() == timedelta(hours=2)


def test_start_of_day_zeroes_time_but_keeps_tz():
    dt = datetime(2026, 8, 17, 15, 30, 45, 123456, tzinfo=ZoneInfo("Europe/Vienna"))
    sd = server._start_of_day(dt)
    assert sd == datetime(2026, 8, 17, 0, 0, 0, 0, tzinfo=ZoneInfo("Europe/Vienna"))
    assert sd.tzinfo is ZoneInfo("Europe/Vienna")
    assert sd.hour == 0
    assert sd.minute == 0
    assert sd.second == 0
    assert sd.microsecond == 0


def test_start_of_day_utc():
    dt = datetime(2026, 8, 17, 15, 30, tzinfo=UTC)
    sd = server._start_of_day(dt)
    assert sd == datetime(2026, 8, 17, tzinfo=UTC)
    assert sd.tzinfo is UTC


def test_parse_dt_date_only_resolves_to_server_local_midnight():
    """_parse_dt() must resolve naive inputs to the server timezone."""
    with mock.patch.object(server, "SERVER_TZ", ZoneInfo("Europe/Vienna")):
        dt = server._parse_dt("2026-08-17")
    assert dt.tzinfo == ZoneInfo("Europe/Vienna")
    assert dt.hour == 0
    assert dt.minute == 0
    assert dt.second == 0
    assert dt.microsecond == 0


def test_parse_dt_naive_datetime_string_resolves_to_server_tz():
    with mock.patch.object(server, "SERVER_TZ", ZoneInfo("Europe/Vienna")):
        dt = server._parse_dt("2026-08-17 10:00:00")
    assert dt.tzinfo == ZoneInfo("Europe/Vienna")
    assert dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-08-17 10:00:00"


def test_parse_dt_explicit_z_suffix_returns_utc():
    with mock.patch.object(server, "SERVER_TZ", ZoneInfo("Europe/Vienna")):
        dt = server._parse_dt("2026-01-01T10:00:00Z")
    assert dt.tzinfo == UTC
    assert dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-01-01 10:00:00"


def test_parse_dt_empty_input_returns_now():
    tz = ZoneInfo("Europe/Vienna")
    fake_now = datetime(2026, 8, 17, 10, 0, 0, tzinfo=tz)
    with mock.patch.object(server, "SERVER_TZ", tz):
        with mock.patch.object(server, "_now", lambda: fake_now):
            assert server._parse_dt("") is fake_now


def test_get_today_events_starts_at_local_midnight():
    """Optional: day helpers pass local-midnight ISO boundaries to get_events."""
    tz = ZoneInfo("Europe/Vienna")
    fake_now = datetime(2026, 8, 18, 15, 30, tzinfo=tz)
    with mock.patch.object(server, "SERVER_TZ", tz):
        with mock.patch.object(server, "_now", lambda: fake_now):
            with mock.patch.object(server, "caldav_get_events") as get_events:
                get_events.return_value = server.ToolResult.success("ok")
                server.caldav_get_today_events()
    _, kwargs = get_events.call_args
    assert kwargs["start"] == "2026-08-18T00:00:00+02:00"
    assert kwargs["end"] == "2026-08-19T00:00:00+02:00"


def test_get_week_events_starts_at_local_midnight():
    tz = ZoneInfo("Europe/Vienna")
    fake_now = datetime(2026, 8, 18, 15, 30, tzinfo=tz)
    with mock.patch.object(server, "SERVER_TZ", tz):
        with mock.patch.object(server, "_now", lambda: fake_now):
            with mock.patch.object(server, "caldav_get_events") as get_events:
                get_events.return_value = server.ToolResult.success("ok")
                server.caldav_get_week_events()
    _, kwargs = get_events.call_args
    assert kwargs["start"] == "2026-08-18T00:00:00+02:00"
    assert kwargs["end"] == "2026-08-25T00:00:00+02:00"
