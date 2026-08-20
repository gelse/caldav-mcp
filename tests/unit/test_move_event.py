"""Unit tests for caldav_move_event.

These tests mock the CalDAV network boundaries and provide a fake source event
with a parsed icalendar component plus a fake destination calendar that captures
what is saved, verifying that the move sets a fresh UID on the component and
serializes via comp.to_ical() rather than raw text replacement.
"""

from conftest import FakeCalendar, FakeEvent, patch_caldav_move
from icalendar import Calendar, Event

import server
from server import Status


def _make_event():
    """Build a Calendar with one VEVENT and return the parsed ical."""
    cal = Calendar()
    cal.add("prodid", "-//caldav-mcp//EN")
    cal.add("version", "2.0")
    ev = Event()
    ev.add("uid", "move-uid@caldav-mcp")
    ev.add("summary", "Moving")
    cal.add_component(ev)
    return cal


def test_move_sets_new_uid_on_component():
    src_cal = FakeCalendar(event=FakeEvent(_make_event()), name="src")
    dst_cal = FakeCalendar(name="dst")
    patchers = patch_caldav_move(src_cal, dst_cal)
    try:
        result = server.caldav_move_event(
            uid="move-uid@caldav-mcp",
            target_calendar="dst",
        )
        assert result.status == Status.OK, result
        # The component's UID changed away from the original.
        ev = src_cal._event.icalendar_component
        new_uid = ev.get("uid")
        assert new_uid != "move-uid@caldav-mcp"
        assert "move-uid@caldav-mcp" in src_cal._event.data
        # The destination received a serialized component containing the new UID.
        assert len(dst_cal.saved) == 1
        assert new_uid in dst_cal.saved[0]
        assert "UID:move-uid@caldav-mcp" not in dst_cal.saved[0]
        # Original was deleted.
        assert src_cal._event.deleted
    finally:
        for p in patchers:
            p.stop()


def test_move_serializes_via_component_not_text_replacement():
    src_cal = FakeCalendar(event=FakeEvent(_make_event()), name="src")
    dst_cal = FakeCalendar(name="dst")
    patchers = patch_caldav_move(src_cal, dst_cal)
    try:
        result = server.caldav_move_event(
            uid="move-uid@caldav-mcp",
            target_calendar="dst",
        )
        assert result.status == Status.OK, result
        # Saved data must parse back into a VEVENT carrying the new UID.
        parsed = Calendar.from_ical(dst_cal.saved[0])
        ev = parsed.walk("VEVENT")[0]
        assert ev.get("uid") != "move-uid@caldav-mcp"
        assert ev.get("summary") == "Moving"
    finally:
        for p in patchers:
            p.stop()


def test_move_returns_new_uid_in_message():
    src_cal = FakeCalendar(event=FakeEvent(_make_event()), name="src")
    dst_cal = FakeCalendar(name="dst")
    patchers = patch_caldav_move(src_cal, dst_cal)
    try:
        result = server.caldav_move_event(
            uid="move-uid@caldav-mcp",
            target_calendar="dst",
        )
        ev = src_cal._event.icalendar_component
        assert f"new uid={ev.get('uid')}" in result.message
    finally:
        for p in patchers:
            p.stop()


def test_move_no_component_returns_error():
    src_event = FakeEvent(_make_event())
    src_cal = FakeCalendar(event=src_event, name="src")
    dst_cal = FakeCalendar(name="dst")
    patchers = patch_caldav_move(src_cal, dst_cal)
    try:
        src_event.icalendar_component = None
        result = server.caldav_move_event(
            uid="move-uid@caldav-mcp",
            target_calendar="dst",
        )
        assert result.status == Status.ERROR
        assert "no icalendar component" in result.message
        # Nothing was saved or deleted.
        assert dst_cal.saved == []
        assert not src_event.deleted
    finally:
        for p in patchers:
            p.stop()
