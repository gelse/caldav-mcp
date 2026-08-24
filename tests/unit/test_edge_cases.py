"""Edge-case unit tests for Unicode, long strings, network failures,
malformed iCalendar data, and special characters in calendar names.
"""

from unittest import mock

from caldav.lib.error import DAVError
from conftest import FakeCalendar, FakeEvent, make_event, patch_caldav
from icalendar import Calendar, Event

import server
from caldav_mcp.calendar import _event_to_dict
from server import Status

# ── Group A: Unicode in Event Fields ─────────────────────────────────


class TestUnicodeEventFields:
    """Tests for Unicode content in event summary, location, and description."""

    def test_create_event_unicode_summary(self):
        """Summary with CJK, emoji, and accented characters."""
        fake_cal = FakeCalendar()
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_create_event(
                summary="会议 🎉 café résumé",
                start="2026-01-01T10:00:00Z",
            )
            assert result.status == Status.OK
        finally:
            for p in patchers:
                p.stop()

    def test_create_event_unicode_location(self):
        """Location with non-Latin scripts."""
        fake_cal = FakeCalendar()
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_create_event(
                summary="s",
                start="2026-01-01T10:00:00Z",
                location="東京タワー москва",
            )
            assert result.status == Status.OK
        finally:
            for p in patchers:
                p.stop()

    def test_create_event_unicode_description(self):
        """Description with mixed scripts and newlines."""
        fake_cal = FakeCalendar()
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_create_event(
                summary="s",
                start="2026-01-01T10:00:00Z",
                description="Line 1\nÜnïcödé ñ\nالعربية",
            )
            assert result.status == Status.OK
        finally:
            for p in patchers:
                p.stop()

    def test_search_events_unicode_query(self):
        """Search with Unicode query string."""
        cal = make_event(summary="会议 测试")
        fake_event = FakeEvent(cal)
        fake_cal = FakeCalendar(event=fake_event)
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_search_events(query="会议")
            assert result.status == Status.OK
            assert len(result.data) >= 1
        finally:
            for p in patchers:
                p.stop()


# ── Group B: Long Strings ───────────────────────────────────────────


class TestLongStrings:
    """Tests for very long string values in event fields."""

    def test_create_event_long_summary(self):
        """Summary exceeding MAX_SUMMARY_LENGTH is rejected by sanitization."""
        long_summary = "A" * 1000
        fake_cal = FakeCalendar()
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_create_event(
                summary=long_summary,
                start="2026-01-01T10:00:00Z",
            )
            assert result.status == Status.ERROR
        finally:
            for p in patchers:
                p.stop()

    def test_create_event_long_description(self):
        """Description exceeding MAX_DESCRIPTION_LENGTH is rejected by sanitization."""
        long_desc = "x" * 5000
        fake_cal = FakeCalendar()
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_create_event(
                summary="s",
                start="2026-01-01T10:00:00Z",
                description=long_desc,
            )
            assert result.status == Status.ERROR
        finally:
            for p in patchers:
                p.stop()


# ── Group C: Network Failures and Timeouts ──────────────────────────


class TestNetworkFailures:
    """Tests for network errors and timeouts during CalDAV operations."""

    def test_list_calendars_network_timeout(self):
        """Timeout during CalDAV connection raises DAVError, caught as ERROR."""
        from caldav_mcp.client_cache import get_cache

        get_cache().clear()
        with (
            mock.patch(
                "caldav_mcp.tools._resolve_credentials",
                return_value=("https://cal.example", "user", "pass"),
            ),
            mock.patch("caldav_mcp.tools.DAVClient", side_effect=DAVError("timed out")),
        ):
            result = server.caldav_list_calendars()
        assert result.status == Status.ERROR

    def test_get_event_by_uid_network_failure(self):
        """Connection reset during event fetch."""
        fake_cal = FakeCalendar()
        patchers = patch_caldav(fake_cal)
        try:
            # Override _get_calendar to raise DAVError
            with mock.patch(
                "caldav_mcp.tools._get_calendar",
                side_effect=DAVError("Connection reset"),
            ):
                result = server.caldav_get_event_by_uid(uid="test-uid")
            assert result.status == Status.ERROR
        finally:
            for p in patchers:
                p.stop()

    def test_create_event_network_failure(self):
        """Network failure during save_event."""
        fake_cal = FakeCalendar()
        fake_cal.save_event = mock.Mock(side_effect=DAVError("Network unreachable"))
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_create_event(
                summary="s",
                start="2026-01-01T10:00:00Z",
            )
            assert result.status == Status.ERROR
        finally:
            for p in patchers:
                p.stop()

    def test_search_events_network_failure(self):
        """DAVError during search."""
        fake_cal = FakeCalendar()
        fake_cal.search = mock.Mock(side_effect=DAVError("Connection refused"))
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_search_events(query="test")
            assert result.status == Status.ERROR
        finally:
            for p in patchers:
                p.stop()


# ── Group D: Malformed iCalendar Data ───────────────────────────────


class TestMalformedCalendarData:
    """Tests for handling of malformed or incomplete iCalendar data."""

    def test_event_to_dict_missing_required_fields(self):
        """Event component with no SUMMARY, no DTSTART."""
        ev = FakeEvent(make_event())
        # Remove optional fields to test graceful handling
        comp = ev.icalendar_component
        del comp["summary"]
        result = _event_to_dict(ev)
        assert result["summary"] == ""
        assert result["uid"] != ""

    def test_event_to_dict_no_icalendar_component(self):
        """Object with no icalendar_component attribute returns fallback dict."""

        class NoComponent:
            id = "fallback-uid"

        result = _event_to_dict(NoComponent())
        assert result["uid"] == "fallback-uid"
        assert result["summary"] == ""
        assert result["dtstart"] == ""

    def test_event_to_dict_empty_event(self):
        """VEVENT with only UID."""
        cal = Calendar()
        ev = Event()
        ev.add("uid", "minimal@test")
        cal.add_component(ev)
        fake_event = FakeEvent(cal)
        result = _event_to_dict(fake_event)
        assert result["uid"] == "minimal@test"
        assert result["summary"] == ""


# ── Group E: Special Characters in Calendar Names ────────────────────


class TestSpecialCalendarNames:
    """Tests for calendars with special characters in names and URLs."""

    def test_calendar_name_with_spaces(self):
        """Calendar name containing spaces."""
        fake_cal = FakeCalendar(name="My Calendar", url="https://cal.example/my%20calendar")
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_list_calendars()
            assert result.status == Status.OK
            assert result.data[0]["name"] == "My Calendar"
        finally:
            for p in patchers:
                p.stop()

    def test_calendar_name_with_unicode(self):
        """Calendar name with Unicode characters."""
        fake_cal = FakeCalendar(name="カレンダー", url="https://cal.example/unicode")
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_list_calendars()
            assert result.status == Status.OK
            assert result.data[0]["name"] == "カレンダー"
        finally:
            for p in patchers:
                p.stop()

    def test_calendar_name_with_special_url_chars(self):
        """Calendar URL with encoded special characters."""
        fake_cal = FakeCalendar(
            name="Work & Personal",
            url="https://cal.example/work%20%26%20personal",
        )
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_list_calendars()
            assert result.status == Status.OK
            assert result.data[0]["url"] == "https://cal.example/work%20%26%20personal"
        finally:
            for p in patchers:
                p.stop()


# ── Group F: Empty and Null Edge Cases ───────────────────────────────


class TestEmptyAndNull:
    """Tests for empty, whitespace-only, and null values."""

    def test_create_event_empty_summary(self):
        """Empty summary should still succeed (summary is optional in iCal)."""
        fake_cal = FakeCalendar()
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_create_event(
                summary="",
                start="2026-01-01T10:00:00Z",
            )
            assert result.status == Status.OK
        finally:
            for p in patchers:
                p.stop()

    def test_create_event_whitespace_only_summary(self):
        """Whitespace-only summary."""
        fake_cal = FakeCalendar()
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_create_event(
                summary="   ",
                start="2026-01-01T10:00:00Z",
            )
            assert result.status == Status.OK
        finally:
            for p in patchers:
                p.stop()

    def test_search_events_empty_query(self):
        """Empty search query returns all events."""
        cal = make_event(summary="Test Event")
        fake_event = FakeEvent(cal)
        fake_cal = FakeCalendar(event=fake_event)
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_search_events(query="")
            assert result.status == Status.OK
        finally:
            for p in patchers:
                p.stop()

    def test_create_event_no_optional_fields(self):
        """Create event with only required fields (summary, start)."""
        fake_cal = FakeCalendar()
        patchers = patch_caldav(fake_cal)
        try:
            result = server.caldav_create_event(
                summary="Minimal",
                start="2026-01-01T10:00:00Z",
            )
            assert result.status == Status.OK
        finally:
            for p in patchers:
                p.stop()
