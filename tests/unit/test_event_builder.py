"""Unit tests for caldav_mcp.event_builder — pure function tests.

These tests exercise build_event and parse_attendee_emails without any
CalDAV mocking, since the functions are pure and have no external dependencies.
"""

from datetime import datetime

from caldav_mcp.constants import (
    MAILTO_PREFIX,
)
from caldav_mcp.event_builder import build_event, parse_attendee_emails


def test_build_event_minimal():
    now = datetime(2026, 1, 1, 12, 0)
    start = datetime(2026, 1, 15, 10, 0)
    end = datetime(2026, 1, 15, 11, 0)
    ical, uid = build_event(summary="Meeting", start_dt=start, end_dt=end, now=now)
    events = ical.walk("VEVENT")
    assert len(events) == 1
    ev = events[0]
    assert str(ev.get("summary")) == "Meeting"
    assert uid.endswith("@caldav-mcp")


def test_build_event_with_attendees():
    now = datetime(2026, 1, 1, 12, 0)
    start = datetime(2026, 1, 15, 10, 0)
    end = datetime(2026, 1, 15, 11, 0)
    ical, _ = build_event(
        summary="Meeting",
        start_dt=start,
        end_dt=end,
        now=now,
        attendee_emails=["a@example.com", "b@example.com"],
    )
    ev = ical.walk("VEVENT")[0]
    attendees = ev.get("attendee")
    assert isinstance(attendees, list)
    assert len(attendees) == 2
    for a in attendees:
        assert str(a).startswith(MAILTO_PREFIX)


def test_parse_attendee_emails_empty():
    assert parse_attendee_emails("") == []
    assert parse_attendee_emails("  ") == []


def test_parse_attendee_emails_single():
    assert parse_attendee_emails("alice@example.com") == ["alice@example.com"]


def test_parse_attendee_emails_multiple():
    result = parse_attendee_emails("a@ex.com, b@ex.com, c@ex.com")
    assert result == ["a@ex.com", "b@ex.com", "c@ex.com"]


def test_parse_attendee_emails_strips_whitespace():
    result = parse_attendee_emails("  a@ex.com ,  b@ex.com  ")
    assert result == ["a@ex.com", "b@ex.com"]
