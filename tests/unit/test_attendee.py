"""Unit tests for caldav_add_attendee and caldav_remove_attendee.

These tests mock the CalDAV network boundaries and provide a fake event with a
parsed icalendar component so we can verify that attendees are managed via the
component API (vCalAddress with ROLE/PARTSTAT/RSVP params) rather than raw text
manipulation.
"""

from conftest import FakeCalendar, FakeEvent, attendees_of, make_event, patch_caldav

import server
from server import Status


def _setup():
    """Build a fake event + calendar, start the network patchers."""
    fake_cal = FakeCalendar(event=FakeEvent(make_event()))
    patchers = patch_caldav(fake_cal)
    return fake_cal, patchers


# ── caldav_add_attendee ────────────────────────────────────────────────


def test_adds_attendee_with_component_api():
    fake_cal, patchers = _setup()
    try:
        result = server.caldav_add_attendee(
            uid="test-uid@caldav-mcp",
            email="alice@example.com",
        )
        assert result.status == Status.OK, result
        attendees = attendees_of(fake_cal._event)
        assert len(attendees) == 1
        a = attendees[0]
        assert str(a) == "mailto:alice@example.com"
        assert a.params.get("PARTSTAT") == "NEEDS-ACTION"
        assert a.params.get("RSVP") == "TRUE"
        assert a.params.get("ROLE") == "REQ-PARTICIPANT"
    finally:
        for p in patchers:
            p.stop()


def test_respects_custom_role():
    fake_cal, patchers = _setup()
    try:
        server.caldav_add_attendee(
            uid="test-uid@caldav-mcp",
            email="bob@example.com",
            role="OPT-PARTICIPANT",
        )
        a = attendees_of(fake_cal._event)[0]
        assert a.params.get("ROLE") == "OPT-PARTICIPANT"
    finally:
        for p in patchers:
            p.stop()


def test_normalizes_email_with_mailto_prefix():
    fake_cal, patchers = _setup()
    try:
        server.caldav_add_attendee(
            uid="test-uid@caldav-mcp",
            email="mailto:carol@example.com",
        )
        a = attendees_of(fake_cal._event)[0]
        assert str(a) == "mailto:carol@example.com"
    finally:
        for p in patchers:
            p.stop()


def test_appends_multiple_attendees():
    fake_cal, patchers = _setup()
    try:
        server.caldav_add_attendee(uid="test-uid@caldav-mcp", email="a@example.com")
        server.caldav_add_attendee(uid="test-uid@caldav-mcp", email="b@example.com")
        emails = sorted(str(a) for a in attendees_of(fake_cal._event))
        assert emails == ["mailto:a@example.com", "mailto:b@example.com"]
    finally:
        for p in patchers:
            p.stop()


def test_no_component_returns_error():
    fake_cal, patchers = _setup()
    try:
        fake_cal._event.icalendar_component = None
        result = server.caldav_add_attendee(
            uid="test-uid@caldav-mcp",
            email="nobody@example.com",
        )
        assert result.status == Status.ERROR
        assert "no icalendar component" in result.message
    finally:
        for p in patchers:
            p.stop()


def test_already_mailto_attendee_not_duplicated():
    fake_cal, patchers = _setup()
    try:
        server.caldav_add_attendee(uid="test-uid@caldav-mcp", email="a@example.com")
        server.caldav_add_attendee(uid="test-uid@caldav-mcp", email="a@example.com")
        assert len(attendees_of(fake_cal._event)) == 2
    finally:
        for p in patchers:
            p.stop()


# ── caldav_remove_attendee ─────────────────────────────────────────────


def test_removes_attendee():
    fake_cal, patchers = _setup()
    try:
        server.caldav_add_attendee(uid="test-uid@caldav-mcp", email="alice@example.com")
        result = server.caldav_remove_attendee(
            uid="test-uid@caldav-mcp",
            email="alice@example.com",
        )
        assert result.status == Status.OK, result
        assert attendees_of(fake_cal._event) == []
    finally:
        for p in patchers:
            p.stop()


def test_remove_handles_mailto_prefix():
    fake_cal, patchers = _setup()
    try:
        server.caldav_add_attendee(uid="test-uid@caldav-mcp", email="alice@example.com")
        result = server.caldav_remove_attendee(
            uid="test-uid@caldav-mcp",
            email="mailto:alice@example.com",
        )
        assert result.status == Status.OK, result
        assert attendees_of(fake_cal._event) == []
    finally:
        for p in patchers:
            p.stop()


def test_remove_is_case_insensitive():
    fake_cal, patchers = _setup()
    try:
        server.caldav_add_attendee(uid="test-uid@caldav-mcp", email="alice@example.com")
        result = server.caldav_remove_attendee(
            uid="test-uid@caldav-mcp",
            email="ALICE@example.com",
        )
        assert result.status == Status.OK, result
        assert attendees_of(fake_cal._event) == []
    finally:
        for p in patchers:
            p.stop()


def test_remove_leaves_other_attendees():
    fake_cal, patchers = _setup()
    try:
        server.caldav_add_attendee(uid="test-uid@caldav-mcp", email="alice@example.com")
        server.caldav_add_attendee(uid="test-uid@caldav-mcp", email="bob@example.com")
        result = server.caldav_remove_attendee(
            uid="test-uid@caldav-mcp",
            email="alice@example.com",
        )
        assert result.status == Status.OK, result
        emails = sorted(str(a) for a in attendees_of(fake_cal._event))
        assert emails == ["mailto:bob@example.com"]
    finally:
        for p in patchers:
            p.stop()


def test_remove_not_found():
    fake_cal, patchers = _setup()
    try:
        server.caldav_add_attendee(uid="test-uid@caldav-mcp", email="alice@example.com")
        result = server.caldav_remove_attendee(
            uid="test-uid@caldav-mcp",
            email="nobody@example.com",
        )
        assert result.status == Status.NOT_FOUND
        assert "not found" in result.message
        assert len(attendees_of(fake_cal._event)) == 1
    finally:
        for p in patchers:
            p.stop()


def test_remove_no_attendees_not_found():
    fake_cal, patchers = _setup()
    try:
        result = server.caldav_remove_attendee(
            uid="test-uid@caldav-mcp",
            email="alice@example.com",
        )
        assert result.status == Status.NOT_FOUND
        assert "not found" in result.message
    finally:
        for p in patchers:
            p.stop()


def test_remove_no_component_returns_error():
    fake_cal, patchers = _setup()
    try:
        fake_cal._event.icalendar_component = None
        result = server.caldav_remove_attendee(
            uid="test-uid@caldav-mcp",
            email="alice@example.com",
        )
        assert result.status == Status.ERROR
        assert "no icalendar component" in result.message
    finally:
        for p in patchers:
            p.stop()
