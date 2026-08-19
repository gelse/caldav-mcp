"""Round-trip (serialize -> re-parse) tests for attendee and move operations.

These tests verify that ATTENDEE properties written through the icalendar
component API survive a full serialise -> re-parse cycle without a live CalDAV
server. Each fake event holds a real ``icalendar.Event`` component, and its
``save()`` re-parses the serialized payload so we can confirm round-trip
fidelity of the ``mailto:`` address as well as CN / PARTSTAT / RSVP / ROLE.
"""

from conftest import (
    FakeCalendar,
    FakeEvent,
    attendees_of,
    make_event,
    patch_caldav,
    patch_caldav_move,
)
from icalendar import Calendar

import server
from server import Status


def test_added_attendee_survives_roundtrip():
    ical = make_event(
        uid="roundtrip-uid@caldav-mcp",
        summary="Roundtrip Meeting",
        attendees=["existing@example.com"],
    )
    event = FakeEvent(ical)
    fake_cal = FakeCalendar(event)
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_add_attendee(
            uid="roundtrip-uid@caldav-mcp",
            email="new@example.com",
        )
        assert result.status == Status.OK, result
        event.save()  # simulate server persisting the serialized payload
        attendees = attendees_of(event)
        emails = sorted(str(a) for a in attendees)
        assert "mailto:existing@example.com" in emails
        new = next(a for a in attendees if str(a) == "mailto:new@example.com")
        assert new.params.get("PARTSTAT") == "NEEDS-ACTION"
        assert new.params.get("RSVP") == "TRUE"
        assert new.params.get("ROLE") == "REQ-PARTICIPANT"
        # The serialized data must re-parse into the same attendee set.
        reparsed = Calendar.from_ical(event.data)
        reparsed_emails = sorted(
            str(a) for a in reparsed.walk("VEVENT")[0].get("attendee", [])
        )
        assert reparsed_emails == emails
    finally:
        for p in patchers:
            p.stop()


def test_added_attendee_serializes_with_mailto_and_params():
    ical = make_event(
        uid="roundtrip-uid@caldav-mcp",
        summary="Roundtrip Meeting",
    )
    event = FakeEvent(ical)
    fake_cal = FakeCalendar(event)
    patchers = patch_caldav(fake_cal)
    try:
        event.saves = 0
        result = server.caldav_add_attendee(
            uid="roundtrip-uid@caldav-mcp",
            email="carol@example.com",
            role="OPT-PARTICIPANT",
        )
        assert result.status == Status.OK, result
        assert event.saves > 0
        payload = event.data
        assert "ATTENDEE" in payload
        assert "Attendee:" not in payload  # no raw text interpolation
        reparsed = Calendar.from_ical(payload)
        reparsed_attendees = reparsed.walk("VEVENT")[0].get("attendee")
        if reparsed_attendees is None:
            reparsed_attendees = []
        elif not isinstance(reparsed_attendees, (list, tuple)):
            reparsed_attendees = [reparsed_attendees]
        carol = next(
            a
            for a in reparsed_attendees
            if str(a) == "mailto:carol@example.com"
        )
        assert carol.params.get("ROLE") == "OPT-PARTICIPANT"
        assert carol.params.get("PARTSTAT") == "NEEDS-ACTION"
        assert carol.params.get("RSVP") == "TRUE"
    finally:
        for p in patchers:
            p.stop()


def test_removed_attendee_does_not_survive_roundtrip():
    ical = make_event(
        uid="roundtrip-uid@caldav-mcp",
        summary="Roundtrip Meeting",
        attendees=["remove@example.com", "keep@example.com"],
    )
    event = FakeEvent(ical)
    fake_cal = FakeCalendar(event)
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_remove_attendee(
            uid="roundtrip-uid@caldav-mcp",
            email="remove@example.com",
        )
        assert result.status == Status.OK, result
        event.save()
        attendees = attendees_of(event)
        emails = [str(a) for a in attendees]
        assert "mailto:remove@example.com" not in emails
        assert "mailto:keep@example.com" in emails
        reparsed = Calendar.from_ical(event.data)
        reparsed_attendees = reparsed.walk("VEVENT")[0].get("attendee")
        if reparsed_attendees is None:
            reparsed_emails = []
        elif not isinstance(reparsed_attendees, (list, tuple)):
            reparsed_emails = [str(reparsed_attendees)]
        else:
            reparsed_emails = [str(a) for a in reparsed_attendees]
        assert reparsed_emails == emails
    finally:
        for p in patchers:
            p.stop()


def test_remove_not_found_returns_not_found():
    ical = make_event(
        uid="roundtrip-uid@caldav-mcp",
        summary="Roundtrip Meeting",
        attendees=["remove@example.com", "keep@example.com"],
    )
    event = FakeEvent(ical)
    fake_cal = FakeCalendar(event)
    patchers = patch_caldav(fake_cal)
    try:
        result = server.caldav_remove_attendee(
            uid="roundtrip-uid@caldav-mcp",
            email="nobody@example.com",
        )
        assert result.status == Status.NOT_FOUND
        assert "not found" in result.message
        event.save()
        emails = [str(a) for a in attendees_of(event)]
        assert sorted(emails) == sorted(
            ["mailto:remove@example.com", "mailto:keep@example.com"]
        )
    finally:
        for p in patchers:
            p.stop()


def test_move_preserves_attendees_with_new_uid():
    ical = make_event(
        uid="move-uid@caldav-mcp",
        summary="Roundtrip Meeting",
        attendees=["alice@example.com"],
    )
    event = FakeEvent(ical)
    src_cal = FakeCalendar(event=event, name="src")
    dst_cal = FakeCalendar(name="dst")
    patchers = patch_caldav_move(src_cal, dst_cal)
    try:
        result = server.caldav_move_event(
            uid="move-uid@caldav-mcp",
            target_calendar="dst",
        )
        assert result.status == Status.OK, result
        assert len(dst_cal.saved) == 1
        # Destination payload must re-parse to a VEVENT with a fresh UID that
        # still carries the original attendee.
        reparsed = Calendar.from_ical(dst_cal.saved[0])
        ev = reparsed.walk("VEVENT")[0]
        assert ev.get("uid") != "move-uid@caldav-mcp"
        assert ev.get("summary") == "Roundtrip Meeting"
        saved_attendees = ev.get("attendee")
        if not isinstance(saved_attendees, (list, tuple)):
            saved_attendees = [saved_attendees]
        assert len(saved_attendees) == 1
        assert str(saved_attendees[0]) == "mailto:alice@example.com"
        # Original was deleted.
        assert event.deleted
    finally:
        for p in patchers:
            p.stop()
