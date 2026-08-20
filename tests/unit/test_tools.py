"""Direct unit tests for all uncovered CalDAV tool handlers.

Covers: list_calendars, get_events, get_event_by_uid, search_events,
get_freebusy, update_event, delete_event, list_attendees,
get_today_events, get_week_events, move_event.

All tests use the fake-network pattern from conftest.py.
"""

from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from conftest import (
    FakeCalendar,
    FakeClient,
    FakeEvent,
    make_event,
    patch_caldav,
    patch_caldav_move,
)
from icalendar import Calendar, Event

import server
from caldav_mcp.client_cache import get_cache
from server import Status


def _build_event(uid="test-uid", summary="Meeting", location="", description="", categories=""):
    """Build a Calendar with one VEVENT carrying the given properties."""
    cal = Calendar()
    cal.add("prodid", "-//caldav-mcp//EN")
    cal.add("version", "2.0")
    ev = Event()
    ev.add("uid", uid)
    ev.add("dtstart", datetime(2026, 1, 15, 10, 0))
    ev.add("dtend", datetime(2026, 1, 15, 11, 0))
    ev.add("summary", summary)
    if location:
        ev.add("location", location)
    if description:
        ev.add("description", description)
    if categories:
        ev.add("categories", categories)
    cal.add_component(ev)
    return cal


# ── Section 1: caldav_list_calendars ────────────────────────────────────


def test_list_calendars_returns_names_and_urls():
    fake_cal = FakeCalendar(name="Work", url="https://cal.example/work")
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_list_calendars()
        assert result.status == Status.OK
        assert len(result.data) == 1
        assert result.data[0] == {"name": "Work", "url": "https://cal.example/work"}
    finally:
        for p in patchers:
            p.stop()


def test_list_calendars_empty():
    get_cache().clear()
    with (
        mock.patch("caldav_mcp.tools._resolve_credentials", return_value=("u", "p", "w")),
        mock.patch("caldav_mcp.tools.DAVClient", return_value=FakeClient(calendars=[])),
    ):
        result = server.caldav_list_calendars()
    assert result.status == Status.EMPTY


def test_list_calendars_auth_error():
    auth_result = server.ToolResult.failure(Status.AUTH, "unauthorized")
    with mock.patch("caldav_mcp.tools._require_auth", return_value=auth_result):
        result = server.caldav_list_calendars()
    assert result.status == Status.AUTH


# ── Section 2: caldav_get_events ─────────────────────────────────────────


def test_get_events_returns_matching_events():
    fake_cal = FakeCalendar(event=_build_event(uid="ev-1", summary="Standup"))
    patchers = patch_caldav(fake_cal)
    tz = ZoneInfo("UTC")
    fake_now = datetime(2026, 1, 15, 8, 0, tzinfo=tz)
    try:
        with (
            mock.patch("caldav_mcp.tools.queries._now", lambda: fake_now),
            mock.patch(
                "caldav_mcp.tools.queries._start_of_day",
                lambda dt: dt.replace(hour=0, minute=0, second=0, microsecond=0),
            ),
        ):
            result = server.caldav_get_events()
        assert result.status == Status.OK
        assert len(result.data) == 1
        assert result.data[0]["uid"] == "ev-1"
    finally:
        for p in patchers:
            p.stop()


def test_get_events_explicit_start_end():
    fake_cal = FakeCalendar(event=_build_event(uid="ev-2"))
    patchers = patch_caldav(fake_cal)
    start_dt = datetime(2026, 1, 15, 9, 0)
    end_dt = datetime(2026, 1, 15, 12, 0)
    try:
        with mock.patch("caldav_mcp.tools.queries._parse_dt", side_effect=[start_dt, end_dt]):
            result = server.caldav_get_events(start="2026-01-15T09:00", end="2026-01-15T12:00")
        assert result.status == Status.OK
        assert len(result.data) == 1
    finally:
        for p in patchers:
            p.stop()


def test_get_events_empty_range():
    fake_cal = FakeCalendar(events=[])
    patchers = patch_caldav(fake_cal)
    tz = ZoneInfo("UTC")
    fake_now = datetime(2026, 1, 15, 8, 0, tzinfo=tz)
    try:
        with (
            mock.patch("caldav_mcp.tools.queries._now", lambda: fake_now),
            mock.patch(
                "caldav_mcp.tools.queries._start_of_day",
                lambda dt: dt.replace(hour=0, minute=0, second=0, microsecond=0),
            ),
        ):
            result = server.caldav_get_events()
        assert result.status == Status.EMPTY
    finally:
        for p in patchers:
            p.stop()


# ── Section 3: caldav_get_event_by_uid ───────────────────────────────────


def test_get_event_by_uid_success():
    fake_cal = FakeCalendar(event=_build_event(uid="uid-1", summary="Standup"))
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_get_event_by_uid(uid="uid-1")
        assert result.status == Status.OK
        assert "uid-1" in result.message
        assert "Standup" in result.message
        assert result.data["uid"] == "uid-1"
    finally:
        for p in patchers:
            p.stop()


def test_get_event_by_uid_not_found():
    fake_cal = FakeCalendar(event=_build_event(uid="uid-1"))
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_get_event_by_uid(uid="unknown")
        assert result.status == Status.NOT_FOUND
    finally:
        for p in patchers:
            p.stop()


def test_get_event_by_uid_auth_error():
    auth_result = server.ToolResult.failure(Status.AUTH, "unauthorized")
    with mock.patch("caldav_mcp.tools._require_auth", return_value=auth_result):
        result = server.caldav_get_event_by_uid(uid="uid-1")
    assert result.status == Status.AUTH


# ── Section 4: caldav_search_events ──────────────────────────────────────


def test_search_events_match():
    ev1 = FakeEvent(_build_event(uid="ev-1", summary="Team Standup"))
    ev2 = FakeEvent(_build_event(uid="ev-2", summary="Lunch Break"))
    fake_cal = FakeCalendar(events=[ev1, ev2])
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_search_events(query="standup")
        assert result.status == Status.OK
        assert len(result.data) == 1
        assert result.data[0]["uid"] == "ev-1"
    finally:
        for p in patchers:
            p.stop()


def test_search_events_no_match():
    fake_cal = FakeCalendar(event=_build_event(uid="ev-1", summary="Meeting"))
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_search_events(query="nonexistent")
        assert result.status == Status.EMPTY
    finally:
        for p in patchers:
            p.stop()


def test_search_events_empty_calendar():
    fake_cal = FakeCalendar(events=[])
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_search_events(query="anything")
        assert result.status == Status.EMPTY
    finally:
        for p in patchers:
            p.stop()


def test_search_events_case_insensitive():
    fake_cal = FakeCalendar(event=_build_event(uid="ev-1", summary="Team Meeting"))
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_search_events(query="meeting")
        assert result.status == Status.OK
        assert len(result.data) == 1
    finally:
        for p in patchers:
            p.stop()


# ── Section 5: caldav_get_freebusy ───────────────────────────────────────


def test_get_freebusy_busy():
    fake_cal = FakeCalendar(event=_build_event(uid="ev-1", summary="Standup"))
    patchers = patch_caldav(fake_cal)
    tz = ZoneInfo("UTC")
    fake_now = datetime(2026, 1, 15, 8, 0, tzinfo=tz)
    try:
        with (
            mock.patch("caldav_mcp.tools.queries._now", lambda: fake_now),
            mock.patch(
                "caldav_mcp.tools.queries._start_of_day",
                lambda dt: dt.replace(hour=0, minute=0, second=0, microsecond=0),
            ),
        ):
            result = server.caldav_get_freebusy()
        assert result.status == Status.OK
        assert "Busy" in result.message
        assert len(result.data) == 1
    finally:
        for p in patchers:
            p.stop()


def test_get_freebusy_free():
    fake_cal = FakeCalendar(events=[])
    patchers = patch_caldav(fake_cal)
    tz = ZoneInfo("UTC")
    fake_now = datetime(2026, 1, 15, 8, 0, tzinfo=tz)
    try:
        with (
            mock.patch("caldav_mcp.tools.queries._now", lambda: fake_now),
            mock.patch(
                "caldav_mcp.tools.queries._start_of_day",
                lambda dt: dt.replace(hour=0, minute=0, second=0, microsecond=0),
            ),
        ):
            result = server.caldav_get_freebusy()
        assert result.status == Status.OK
        assert "Free" in result.message
        assert result.data == []
    finally:
        for p in patchers:
            p.stop()


def test_get_freebusy_explicit_range():
    fake_cal = FakeCalendar(event=_build_event(uid="ev-1"))
    patchers = patch_caldav(fake_cal)
    start_dt = datetime(2026, 1, 15, 9, 0)
    end_dt = datetime(2026, 1, 15, 12, 0)
    try:
        with mock.patch("caldav_mcp.tools.queries._parse_dt", side_effect=[start_dt, end_dt]):
            result = server.caldav_get_freebusy(start="2026-01-15T09:00", end="2026-01-15T12:00")
        assert result.status == Status.OK
        assert "Busy" in result.message
    finally:
        for p in patchers:
            p.stop()


# ── Section 6: caldav_update_event ───────────────────────────────────────


def test_update_event_summary():
    fake_cal = FakeCalendar(event=_build_event(uid="uid-1", summary="Old Title"))
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_update_event(uid="uid-1", summary="New Title")
        assert result.status == Status.OK
        comp = fake_cal._event.icalendar_component
        assert str(comp.get("summary")) == "New Title"
        assert fake_cal._event.saves >= 1
    finally:
        for p in patchers:
            p.stop()


def test_update_event_not_found():
    fake_cal = FakeCalendar(event=_build_event(uid="uid-1"))
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_update_event(uid="unknown", summary="New Title")
        assert result.status == Status.NOT_FOUND
    finally:
        for p in patchers:
            p.stop()


def test_update_event_multiple_fields():
    fake_cal = FakeCalendar(event=_build_event(uid="uid-1"))
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_update_event(
            uid="uid-1",
            summary="New Summary",
            location="New Location",
            description="New Description",
        )
        assert result.status == Status.OK
        comp = fake_cal._event.icalendar_component
        assert str(comp.get("summary")) == "New Summary"
        assert str(comp.get("location")) == "New Location"
        assert str(comp.get("description")) == "New Description"
    finally:
        for p in patchers:
            p.stop()


# ── Section 7: caldav_delete_event ───────────────────────────────────────


def test_delete_event_success():
    fake_cal = FakeCalendar(event=_build_event(uid="uid-1"))
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_delete_event(uid="uid-1")
        assert result.status == Status.OK
        assert fake_cal._event.deleted is True
    finally:
        for p in patchers:
            p.stop()


def test_delete_event_not_found():
    fake_cal = FakeCalendar(event=_build_event(uid="uid-1"))
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_delete_event(uid="unknown")
        assert result.status == Status.NOT_FOUND
    finally:
        for p in patchers:
            p.stop()


# ── Section 8: caldav_list_attendees ─────────────────────────────────────


def test_list_attendees_with_attendees():
    event = FakeEvent(make_event(attendees=["a@ex.com", "b@ex.com"]))
    fake_cal = FakeCalendar(event=event)
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_list_attendees(uid="test-uid@caldav-mcp")
        assert result.status == Status.OK
        assert len(result.data) == 2
    finally:
        for p in patchers:
            p.stop()


def test_list_attendees_no_attendees():
    event = FakeEvent(make_event())
    fake_cal = FakeCalendar(event=event)
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_list_attendees(uid="test-uid@caldav-mcp")
        assert result.status == Status.EMPTY
    finally:
        for p in patchers:
            p.stop()


def test_list_attendees_auth_error():
    auth_result = server.ToolResult.failure(Status.AUTH, "unauthorized")
    with mock.patch("caldav_mcp.tools._require_auth", return_value=auth_result):
        result = server.caldav_list_attendees(uid="test-uid@caldav-mcp")
    assert result.status == Status.AUTH


# ── Section 9: caldav_get_today_events ───────────────────────────────────


def test_get_today_events_delegates():
    mock_result = server.ToolResult.success(message="ok", data=[{"uid": "x"}])
    with (
        mock.patch("caldav_mcp.tools.queries._require_auth", return_value=None),
        mock.patch("caldav_mcp.tools.queries.caldav_get_events", return_value=mock_result) as mock_get,
    ):
        result = server.caldav_get_today_events(calendar_name="Work")
    assert result is mock_result
    mock_get.assert_called_once()
    call_kwargs = mock_get.call_args[1]
    assert call_kwargs["calendar_name"] == "Work"
    assert "start" in call_kwargs
    assert "end" in call_kwargs


def test_get_today_events_auth_error():
    auth_result = server.ToolResult.failure(Status.AUTH, "unauthorized")
    with mock.patch("caldav_mcp.tools.queries._require_auth", return_value=auth_result):
        result = server.caldav_get_today_events()
    assert result.status == Status.AUTH


# ── Section 10: caldav_get_week_events ───────────────────────────────────


def test_get_week_events_delegates():
    mock_result = server.ToolResult.success(message="ok", data=[{"uid": "x"}])
    with (
        mock.patch("caldav_mcp.tools.queries._require_auth", return_value=None),
        mock.patch("caldav_mcp.tools.queries.caldav_get_events", return_value=mock_result) as mock_get,
    ):
        result = server.caldav_get_week_events(calendar_name="Work")
    assert result is mock_result
    mock_get.assert_called_once()
    call_kwargs = mock_get.call_args[1]
    assert call_kwargs["calendar_name"] == "Work"
    assert "start" in call_kwargs
    assert "end" in call_kwargs


def test_get_week_events_auth_error():
    auth_result = server.ToolResult.failure(Status.AUTH, "unauthorized")
    with mock.patch("caldav_mcp.tools.queries._require_auth", return_value=auth_result):
        result = server.caldav_get_week_events()
    assert result.status == Status.AUTH


# ── Section 11: caldav_move_event additional tests ───────────────────────


def test_move_event_not_found():
    src_cal = FakeCalendar(event=FakeEvent(_build_event(uid="uid-1")), name="src")
    dst_cal = FakeCalendar(name="dst")
    patchers = patch_caldav_move(src_cal, dst_cal)
    try:
        result = server.caldav_move_event(uid="unknown-uid", target_calendar="dst")
        assert result.status == Status.NOT_FOUND
    finally:
        for p in patchers:
            p.stop()


def test_move_event_auth_error():
    auth_result = server.ToolResult.failure(Status.AUTH, "unauthorized")
    with mock.patch("caldav_mcp.auth._require_auth", return_value=auth_result):
        result = server.caldav_move_event(uid="uid-1", target_calendar="dst")
    assert result.status == Status.AUTH
