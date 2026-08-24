"""Unit tests for the RFC 5545 escaping fix in caldav_create_event.

These tests mock the CalDAV network boundaries so we can capture and
round-trip the serialized iCal payload without a live server.
"""

from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from conftest import FakeCalendar, patch_caldav
from icalendar import Calendar

import server
from server import Status


def _create(fake_cal, **kwargs):
    result = server.caldav_create_event(**kwargs)
    assert result.status == Status.OK, f"call failed: {result!r}"
    payload = fake_cal.last_saved
    assert payload is not None, "no payload was saved"
    return Calendar.from_ical(payload)


def _event(parsed):
    """Return the single VEVENT subcomponent of a parsed calendar."""
    events = parsed.walk("VEVENT")
    assert len(events) == 1
    return events[0]


def test_summary_with_special_characters_round_trips():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        summary = "a,b;c\\d\ne"
        parsed = _create(fake_cal, summary=summary, start="2026-01-01T10:00:00Z")
        assert str(_event(parsed)["summary"]) == summary
    finally:
        for p in patchers:
            p.stop()


def test_location_and_description_special_characters():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        location = "Room 1, Building A"
        description = "line1\nline2"
        parsed = _create(
            fake_cal,
            summary="s",
            start="2026-01-01T10:00:00Z",
            location=location,
            description=description,
        )
        ev = _event(parsed)
        assert str(ev["location"]) == location
        assert str(ev["description"]) == description
    finally:
        for p in patchers:
            p.stop()


def test_multiple_attendees():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        parsed = _create(
            fake_cal,
            summary="s",
            start="2026-01-01T10:00:00Z",
            attendees="a@example.com, b@example.com",
        )
        ev = _event(parsed)
        attendees = ev.get("attendee")
        if not isinstance(attendees, (list, tuple)):
            attendees = [attendees]
        assert len(attendees) == 2
        emails = sorted(str(a) for a in attendees)
        assert emails == ["mailto:a@example.com", "mailto:b@example.com"]
        for a in attendees:
            params = a.params
            assert params.get("PARTSTAT") == "NEEDS-ACTION"
            assert params.get("RSVP") == "TRUE"
    finally:
        for p in patchers:
            p.stop()


def test_emoji_in_summary():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        summary = "🎉 party"
        parsed = _create(fake_cal, summary=summary, start="2026-01-01T10:00:00Z")
        assert str(_event(parsed)["summary"]) == summary
    finally:
        for p in patchers:
            p.stop()


def test_empty_optional_fields():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        parsed = _create(fake_cal, summary="s", start="2026-01-01T10:00:00Z")
        ev = _event(parsed)
        assert "uid" in ev
        assert "dtstart" in ev
        assert "dtend" in ev
        assert "dtstamp" in ev
        assert ev.get("summary") == "s"
        assert "location" not in ev
        assert "description" not in ev
        assert "categories" not in ev
        assert "priority" not in ev
        assert "rrule" not in ev
        assert "attendee" not in ev
    finally:
        for p in patchers:
            p.stop()


def test_valid_priority():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        parsed = _create(fake_cal, summary="s", start="2026-01-01T10:00:00Z", priority="5")
        assert int(_event(parsed)["priority"]) == 5
    finally:
        for p in patchers:
            p.stop()


def test_invalid_priority_non_integer():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_create_event(
            summary="s", start="2026-01-01T10:00:00Z", priority="high"
        )
        assert result.status == Status.ERROR
        assert "priority must be an integer" in result.message
    finally:
        for p in patchers:
            p.stop()


def test_invalid_priority_out_of_range():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_create_event(
            summary="s", start="2026-01-01T10:00:00Z", priority="10"
        )
        assert result.status == Status.ERROR
        assert "priority must be between 0 and 9" in result.message
    finally:
        for p in patchers:
            p.stop()


def test_invalid_rrule():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_create_event(
            summary="s", start="2026-01-01T10:00:00Z", rrule="FREQ=BOGUS"
        )
        assert result.status == Status.ERROR
        assert "invalid RRULE" in result.message
    finally:
        for p in patchers:
            p.stop()


def test_valid_rrule():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        parsed = _create(
            fake_cal,
            summary="s",
            start="2026-01-01T10:00:00Z",
            rrule="FREQ=DAILY;COUNT=5",
        )
        assert "rrule" in _event(parsed)
    finally:
        for p in patchers:
            p.stop()


def test_dtstamp_uses_timezone_aware_now():
    """DTSTAMP must derive from the tz-aware _now() in the server timezone.

    Previously this used a hardcoded datetime.now(timezone.utc). After the
    fix the value comes from _now(), which carries SERVER_TZ, and when
    serialized to iCal it must match the corresponding UTC instant.
    """
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        vienna = ZoneInfo("Europe/Vienna")
        fake_now = datetime(2026, 1, 1, 10, 0, 0, tzinfo=vienna)
        with mock.patch("caldav_mcp.datetime_utils.SERVER_TZ", vienna):
            with mock.patch("caldav_mcp.tools.mutations._now", lambda: fake_now):
                result = server.caldav_create_event(summary="s", start="2026-01-01T10:00:00Z")
        assert result.status == Status.OK, f"call failed: {result!r}"
        payload = fake_cal.last_saved
        assert payload is not None, "no payload was saved"
        # 10:00 in Europe/Vienna (UTC+1 in January) == 09:00Z.
        assert "DTSTAMP:20260101T090000Z" in payload
    finally:
        for p in patchers:
            p.stop()
