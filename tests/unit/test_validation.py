"""Validation tests for priority and rrule helpers plus end-to-end rejection.

This module (from plan 11c) targets the extracted helpers ``_validate_priority``
(plan 11a) and ``_validate_rrule`` (plan 11b), and additionally confirms that
``caldav_create_event`` rejects invalid input with clear ``ERROR:`` strings.
The end-to-end tests reuse the fake-network pattern from
``tests/test_create_event.py`` so everything stays network-free and deterministic.
"""

from conftest import FakeCalendar, patch_caldav

import server
from caldav_mcp.calendar import _validate_priority, _validate_rrule
from server import Status


def test_priority_empty_returns_none():
    assert _validate_priority("") == (None, None)


def test_priority_valid():
    assert _validate_priority("0") == (0, None)
    assert _validate_priority("9") == (9, None)


def test_priority_non_integer():
    _, err = _validate_priority("abc")
    assert err == "priority must be an integer"


def test_priority_out_of_range():
    _, err = _validate_priority("10")
    assert err == "priority must be between 0 and 9"
    _, err = _validate_priority("-1")
    assert err == "priority must be between 0 and 9"


def test_rrule_empty_is_true():
    assert _validate_rrule("") is True


def test_rrule_valid_daily():
    assert _validate_rrule("FREQ=DAILY") is True


def test_rrule_invalid():
    assert _validate_rrule("NOT-A-RRULE;;") is False


def test_invalid_priority_non_integer_returns_error():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_create_event(
            summary="s", start="2026-01-01T10:00:00Z", priority="abc"
        )
        assert result.status == Status.ERROR
        assert "priority must be an integer" in result.message
        assert fake_cal.saved == []
    finally:
        for p in patchers:
            p.stop()


def test_invalid_priority_out_of_range_returns_error():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_create_event(
            summary="s", start="2026-01-01T10:00:00Z", priority="10"
        )
        assert result.status == Status.ERROR
        assert "priority must be between 0 and 9" in result.message
        assert fake_cal.saved == []
    finally:
        for p in patchers:
            p.stop()


def test_invalid_rrule_returns_error():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_create_event(
            summary="s", start="2026-01-01T10:00:00Z", rrule="NOT-A-RRULE;;"
        )
        assert result.status == Status.ERROR
        assert "invalid RRULE" in result.message
        assert fake_cal.saved == []
    finally:
        for p in patchers:
            p.stop()


def test_valid_priority_and_rrule_succeed():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_create_event(
            summary="s",
            start="2026-01-01T10:00:00Z",
            priority="5",
            rrule="FREQ=DAILY;COUNT=5",
        )
        assert result.status == Status.OK, f"call failed: {result!r}"
        assert fake_cal.saved
    finally:
        for p in patchers:
            p.stop()


def test_empty_priority_and_empty_rrule_succeed():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_create_event(summary="s", start="2026-01-01T10:00:00Z")
        assert result.status == Status.OK, f"call failed: {result!r}"
        assert fake_cal.saved
    finally:
        for p in patchers:
            p.stop()
