"""Round-trip (serialize -> re-parse -> read) tests for caldav_create_event.

These tests verify that a fully-populated event (summary, dtstart/dtend,
location, description, categories, attendees, priority, rrule) survives a
complete round trip:

    caldav_create_event -> cal.save_event(ical.to_ical()) -> from_ical re-parse

and that the resulting component can be read back through the same helpers
used by ``caldav_get_event_by_uid`` (``_event_to_dict``). The CalDAV network
boundary is mocked with in-memory fakes, mirroring ``test_create_event.py``.
"""

from conftest import FakeCalendar, patch_caldav
from icalendar import Calendar

import server
from server import Status


class FakeObject:
    """Generic stand-in carrying an ``icalendar_component`` attribute.

    ``caldav_get_event_by_uid`` reads ``event.id`` (uid) and relies on
    ``_comp(event)`` returning the ``icalendar_component`` attribute, so a
    plain holder wrapped around a real ``Event`` is all the read path needs.
    """

    def __init__(self, comp):
        self.icalendar_component = comp
        self.id = str(comp.get("uid"))


def _create(fake_cal, **kwargs):
    """Create an event and return the re-parsed VEVENT component."""
    summary = kwargs.get("summary", "s")
    result = server.caldav_create_event(**kwargs)
    assert result.status == Status.OK, result
    assert f"Event '{summary}'" in result.message
    parsed = Calendar.from_ical(fake_cal.last_saved)
    events = parsed.walk("VEVENT")
    assert len(events) == 1
    return FakeObject(events[0])


def _event_from_payload(fake_cal, index=-1):
    parsed = Calendar.from_ical(fake_cal.saved[index])
    events = parsed.walk("VEVENT")
    assert len(events) == 1
    return events[0]


def test_full_event_round_trips_all_fields():
    """A fully-populated event survives serialize -> re-parse -> read."""
    summary = "Back\\slash, comma; and\nnewline"
    location = "Room 3, Building B; Floor 2"
    description = "line one\nline two"
    categories = "Work, Important"
    attendees = "alice@example.com, bob@example.com"
    start = "2026-06-01T10:00:00Z"
    end = "2026-06-01T11:30:00Z"

    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        ev = _create(
            fake_cal,
            summary=summary,
            start=start,
            end=end,
            location=location,
            description=description,
            categories=categories,
            priority="5",
            rrule="FREQ=DAILY;COUNT=5",
            attendees=attendees,
        )

        # Re-parse the raw saved payload once more to confirm full fidelity.
        raw = _event_from_payload(fake_cal)
        assert str(raw["summary"]) == summary
        assert str(raw["location"]) == location
        assert str(raw["description"]) == description
        # CATEGORIES is serialised as a single value with escaped separators
        # ("Work\, Important") and decodes back to the plain comma-joined string.
        assert raw.decoded("categories") == [categories]
        assert int(raw["priority"]) == 5
        assert "rrule" in raw
        assert "dtstart" in raw
        assert "dtend" in raw

        # The read path (_event_to_dict) used by caldav_get_event_by_uid
        # reflects the same text values after the re-parse.
        d = server._event_to_dict(ev)
        assert d["summary"] == summary
        assert d["location"] == location
        assert d["description"] == description
        assert d["uid"] == str(raw["uid"])
    finally:
        for p in patchers:
            p.stop()


def test_summary_with_special_characters_round_trips():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        summary = "a,b;c\\d\ne"
        ev = _create(fake_cal, summary=summary, start="2026-01-01T10:00:00Z")
        assert str(ev.icalendar_component["summary"]) == summary
        assert server._event_to_dict(ev)["summary"] == summary
    finally:
        for p in patchers:
            p.stop()


def test_dtstart_dtend_round_trip_utc():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        ev = _create(
            fake_cal,
            summary="s",
            start="2026-03-05T09:00:00Z",
            end="2026-03-05T10:30:00Z",
        )
        d = server._event_to_dict(ev)
        assert d["dtstart"] == "2026-03-05T09:00:00+00:00"
        assert d["dtend"] == "2026-03-05T10:30:00+00:00"
    finally:
        for p in patchers:
            p.stop()


def test_attendees_round_trip():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        _create(
            fake_cal,
            summary="s",
            start="2026-01-01T10:00:00Z",
            attendees="alice@example.com, bob@example.com",
        )
        parsed_ev = _event_from_payload(fake_cal)
        attendees = parsed_ev.get("attendee")
        if not isinstance(attendees, (list, tuple)):
            attendees = [attendees]
        emails = sorted(str(a) for a in attendees)
        assert emails == ["mailto:alice@example.com", "mailto:bob@example.com"]
        for a in attendees:
            assert a.params.get("PARTSTAT") == "NEEDS-ACTION"
            assert a.params.get("RSVP") == "TRUE"
            assert a.params.get("ROLE") == "REQ-PARTICIPANT"
    finally:
        for p in patchers:
            p.stop()


def test_priority_round_trip():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        _create(
            fake_cal,
            summary="s",
            start="2026-01-01T10:00:00Z",
            priority="7",
        )
        assert int(_event_from_payload(fake_cal)["priority"]) == 7
    finally:
        for p in patchers:
            p.stop()


def test_rrule_round_trip():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        _create(
            fake_cal,
            summary="s",
            start="2026-01-01T10:00:00Z",
            end="2026-01-01T11:00:00Z",
            rrule="FREQ=WEEKLY;BYDAY=MO;COUNT=10",
        )
        assert "rrule" in _event_from_payload(fake_cal)
    finally:
        for p in patchers:
            p.stop()


def test_empty_optional_fields_absent_after_roundtrip():
    fake_cal = FakeCalendar()
    patchers = patch_caldav(fake_cal)
    try:
        _create(fake_cal, summary="s", start="2026-01-01T10:00:00Z")
        raw = _event_from_payload(fake_cal)
        assert "uid" in raw
        assert "dtstart" in raw
        assert "dtend" in raw
        assert "dtstamp" in raw
        assert "location" not in raw
        assert "description" not in raw
        assert "categories" not in raw
        assert "priority" not in raw
        assert "rrule" not in raw
        assert "attendee" not in raw
    finally:
        for p in patchers:
            p.stop()
