"""Unit tests for the pure serialization helpers in server.py.

Covers ``_comp``, ``_event_to_dict`` and ``_attendee_str``. These are pure
functions over ``icalendar`` components, so no CalDAV network mocking is
needed -- we build real ``icalendar.Event`` / ``icalendar.vCalAddress``
objects and pass lightweight wrappers that expose ``icalendar_component``.
"""

from datetime import datetime

from icalendar import Calendar, Event, vCalAddress

from caldav_mcp.calendar import _attendee_str, _comp, _event_to_dict


def make_event_wrapper(component):
    """Wrap an icalendar component so server._comp can find it."""

    class Wrapper:
        def __init__(self, comp):
            self.icalendar_component = comp

    return Wrapper(component)


def _build_event():
    event = Event()
    event.add("uid", "event-123")
    event.add("summary", "Team standup")
    event.add("dtstart", datetime(2026, 8, 18, 9, 0, 0))
    event.add("dtend", datetime(2026, 8, 18, 9, 30, 0))
    event.add("location", "Room 1")
    event.add("description", "Sync on the roadmap")
    return event


def test_comp_returns_attached_component():
    comp = Event()
    wrapper = make_event_wrapper(comp)
    assert _comp(wrapper) is comp


def test_comp_returns_none_without_component():
    class Bare:
        pass

    assert _comp(Bare()) is None


def test_event_to_dict_maps_all_fields():
    cal = Calendar()
    cal.add_component(_build_event())
    wrapper = make_event_wrapper(cal.walk("VEVENT")[0])

    result = _event_to_dict(wrapper)

    assert isinstance(result, dict)
    assert list(result.keys()) == [
        "uid",
        "summary",
        "dtstart",
        "dtend",
        "location",
        "description",
        "categories",
        "attendees",
    ]
    assert result["uid"] == "event-123"
    assert result["summary"] == "Team standup"
    assert result["location"] == "Room 1"
    assert result["description"] == "Sync on the roadmap"
    assert result["attendees"] == ""
    assert result["categories"] == ""


def test_event_to_dict_dtstart_isoformat():
    cal = Calendar()
    cal.add_component(_build_event())
    wrapper = make_event_wrapper(cal.walk("VEVENT")[0])

    result = _event_to_dict(wrapper)
    # Naive datetimes are serialized via isoformat, e.g. 2026-08-18T09:00:00
    assert result["dtstart"] == "2026-08-18T09:00:00"
    assert result["dtend"] == "2026-08-18T09:30:00"


def test_event_to_dict_with_attendees():
    event = _build_event()
    event.add("attendee", vCalAddress("mailto:alice@example.com"))
    cal = Calendar()
    cal.add_component(event)
    wrapper = make_event_wrapper(cal.walk("VEVENT")[0])

    result = _event_to_dict(wrapper)
    assert result["attendees"] == "mailto:alice@example.com"


def test_event_to_dict_multiple_attendees_semicolon_joined():
    event = _build_event()
    event.add("attendee", vCalAddress("mailto:alice@example.com"))
    event.add("attendee", vCalAddress("mailto:bob@example.com"))
    cal = Calendar()
    cal.add_component(event)
    wrapper = make_event_wrapper(cal.walk("VEVENT")[0])

    result = _event_to_dict(wrapper)
    assert result["attendees"] == "mailto:alice@example.com; mailto:bob@example.com"


def test_event_to_dict_missing_component_uses_event_id():
    class BareEvent:
        id = "fallback-id"

    result = _event_to_dict(BareEvent())
    assert result["uid"] == "fallback-id"
    assert result["summary"] == ""
    assert result["attendees"] == ""


def test_attendee_str_with_roled_partstat():
    attendee = vCalAddress("mailto:alice@example.com")
    attendee.params["ROLE"] = "CHAIR"
    attendee.params["PARTSTAT"] = "ACCEPTED"

    assert _attendee_str(attendee) == (
        "mailto:alice@example.com ROLE=CHAIR PARTSTAT=ACCEPTED"
    )


def test_attendee_str_with_role_only():
    attendee = vCalAddress("mailto:bob@example.com")
    attendee.params["ROLE"] = "REQ-PARTICIPANT"

    assert _attendee_str(attendee) == ("mailto:bob@example.com ROLE=REQ-PARTICIPANT")


def test_attendee_str_no_params():
    attendee = vCalAddress("mailto:carol@example.com")

    assert _attendee_str(attendee) == "mailto:carol@example.com"
