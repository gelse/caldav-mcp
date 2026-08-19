"""Unit tests for the timezone behavior introduced in plan 02.

These tests exercise only pure, network-free helpers (``_server_tz``,
``_now``, ``_start_of_day``, ``_parse_dt``) so they run without a live
CalDAV server. They cover the timezone resolution policy (TZ env var with
fallback to UTC) and the server-timezone-aware day-boundary / date-only
parsing behavior.
"""

import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import server


class ServerTzResolutionTest(unittest.TestCase):
    """_server_tz() must honor the TZ env var and fall back to UTC."""

    def test_tz_set_returns_configured_zone(self):
        with mock.patch.dict(server.os.environ, {"TZ": "Europe/Vienna"}):
            tz = server._server_tz()
        self.assertEqual(str(tz), "Europe/Vienna")

    def test_tz_unset_returns_utc(self):
        with mock.patch.dict(server.os.environ, {}, clear=True):
            tz = server._server_tz()
        self.assertIs(tz, UTC)

    def test_tz_empty_returns_utc(self):
        with mock.patch.dict(server.os.environ, {"TZ": ""}):
            tz = server._server_tz()
        self.assertIs(tz, UTC)

    def test_tz_invalid_falls_back_to_utc(self):
        with mock.patch.dict(server.os.environ, {"TZ": "Not/AZone"}):
            tz = server._server_tz()
        self.assertIs(tz, UTC)


class NowAndStartOfDayTest(unittest.TestCase):
    """_now() and _start_of_day() must preserve the server timezone."""

    def test_now_carries_server_timezone(self):
        with mock.patch.object(server, "SERVER_TZ", ZoneInfo("Europe/Vienna")):
            now = server._now()
        self.assertIs(now.tzinfo, ZoneInfo("Europe/Vienna"))
        # Europe/Vienna in late August is on DST (UTC+2).
        self.assertEqual(now.utcoffset(), timedelta(hours=2))

    def test_start_of_day_zeroes_time_but_keeps_tz(self):
        dt = datetime(2026, 8, 17, 15, 30, 45, 123456, tzinfo=ZoneInfo("Europe/Vienna"))
        sd = server._start_of_day(dt)
        self.assertEqual(
            sd,
            datetime(2026, 8, 17, 0, 0, 0, 0, tzinfo=ZoneInfo("Europe/Vienna")),
        )
        self.assertIs(sd.tzinfo, ZoneInfo("Europe/Vienna"))
        self.assertEqual(sd.hour, 0)
        self.assertEqual(sd.minute, 0)
        self.assertEqual(sd.second, 0)
        self.assertEqual(sd.microsecond, 0)

    def test_start_of_day_utc(self):
        dt = datetime(2026, 8, 17, 15, 30, tzinfo=UTC)
        sd = server._start_of_day(dt)
        self.assertEqual(sd, datetime(2026, 8, 17, tzinfo=UTC))
        self.assertIs(sd.tzinfo, UTC)


class ParseDtServerTimezoneTest(unittest.TestCase):
    """_parse_dt() must resolve naive inputs to the server timezone."""

    def test_date_only_resolves_to_server_local_midnight(self):
        with mock.patch.object(server, "SERVER_TZ", ZoneInfo("Europe/Vienna")):
            dt = server._parse_dt("2026-08-17")
        self.assertEqual(dt.tzinfo, ZoneInfo("Europe/Vienna"))
        self.assertEqual(dt.hour, 0)
        self.assertEqual(dt.minute, 0)
        self.assertEqual(dt.second, 0)
        self.assertEqual(dt.microsecond, 0)

    def test_naive_datetime_string_resolves_to_server_tz(self):
        with mock.patch.object(server, "SERVER_TZ", ZoneInfo("Europe/Vienna")):
            dt = server._parse_dt("2026-08-17 10:00:00")
        self.assertEqual(dt.tzinfo, ZoneInfo("Europe/Vienna"))
        self.assertEqual(dt.strftime("%Y-%m-%d %H:%M:%S"), "2026-08-17 10:00:00")

    def test_explicit_z_suffix_returns_utc(self):
        with mock.patch.object(server, "SERVER_TZ", ZoneInfo("Europe/Vienna")):
            dt = server._parse_dt("2026-01-01T10:00:00Z")
        self.assertEqual(dt.tzinfo, UTC)
        self.assertEqual(dt.strftime("%Y-%m-%d %H:%M:%S"), "2026-01-01 10:00:00")

    def test_empty_input_returns_now(self):
        tz = ZoneInfo("Europe/Vienna")
        fake_now = datetime(2026, 8, 17, 10, 0, 0, tzinfo=tz)
        with mock.patch.object(server, "SERVER_TZ", tz):
            with mock.patch.object(server, "_now", lambda: fake_now):
                self.assertIs(server._parse_dt(""), fake_now)


class GetTodayHelpersTest(unittest.TestCase):
    """Optional: day helpers pass local-midnight ISO boundaries to get_events."""

    def test_get_today_events_starts_at_local_midnight(self):
        tz = ZoneInfo("Europe/Vienna")
        fake_now = datetime(2026, 8, 18, 15, 30, tzinfo=tz)
        with mock.patch.object(server, "SERVER_TZ", tz):
            with mock.patch.object(server, "_now", lambda: fake_now):
                with mock.patch.object(server, "caldav_get_events") as get_events:
                    get_events.return_value = server.ToolResult.success("ok")
                    server.caldav_get_today_events()
        _, kwargs = get_events.call_args
        self.assertEqual(kwargs["start"], "2026-08-18T00:00:00+02:00")
        self.assertEqual(kwargs["end"], "2026-08-19T00:00:00+02:00")

    def test_get_week_events_starts_at_local_midnight(self):
        tz = ZoneInfo("Europe/Vienna")
        fake_now = datetime(2026, 8, 18, 15, 30, tzinfo=tz)
        with mock.patch.object(server, "SERVER_TZ", tz):
            with mock.patch.object(server, "_now", lambda: fake_now):
                with mock.patch.object(server, "caldav_get_events") as get_events:
                    get_events.return_value = server.ToolResult.success("ok")
                    server.caldav_get_week_events()
        _, kwargs = get_events.call_args
        self.assertEqual(kwargs["start"], "2026-08-18T00:00:00+02:00")
        self.assertEqual(kwargs["end"], "2026-08-25T00:00:00+02:00")


if __name__ == "__main__":
    unittest.main()
