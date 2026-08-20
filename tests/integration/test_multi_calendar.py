"""Integration tests: operations across multiple calendars."""

from datetime import datetime

import pytest
from icalendar import Calendar, Event

pytestmark = [pytest.mark.integration, pytest.mark.slow]


class TestMultiCalendar:
    """Tests involving multiple calendars on the same server."""

    def test_event_in_different_calendars(self, caldav_client):
        """Create events in two different calendars, verify isolation."""
        principal = caldav_client.principal()
        cal1 = principal.make_calendar(name="multi-cal-1")
        cal2 = principal.make_calendar(name="multi-cal-2")
        try:
            # Create event in cal1
            ev1 = Event()
            ev1.add("uid", "multi-event-1@test")
            ev1.add("summary", "Event in Calendar 1")
            ev1.add("dtstart", datetime(2026, 3, 1, 10, 0))
            ev1.add("dtend", datetime(2026, 3, 1, 11, 0))
            c1 = Calendar()
            c1.add_component(ev1)
            cal1.save_event(c1.to_ical())

            # Create event in cal2
            ev2 = Event()
            ev2.add("uid", "multi-event-2@test")
            ev2.add("summary", "Event in Calendar 2")
            ev2.add("dtstart", datetime(2026, 3, 2, 10, 0))
            ev2.add("dtend", datetime(2026, 3, 2, 11, 0))
            c2 = Calendar()
            c2.add_component(ev2)
            cal2.save_event(c2.to_ical())

            # Verify isolation
            cal1_events = cal1.events()
            cal2_events = cal2.events()
            assert len(cal1_events) == 1
            assert len(cal2_events) == 1
            assert "multi-event-1@test" in str(cal1_events[0].icalendar_component.get("uid"))
            assert "multi-event-2@test" in str(cal2_events[0].icalendar_component.get("uid"))
        finally:
            cal1.delete()
            cal2.delete()

    def test_list_calendars_returns_all(self, caldav_client):
        """Verify all created calendars are listed."""
        principal = caldav_client.principal()
        initial_count = len(principal.calendars())
        cal = principal.make_calendar(name="list-cal-test")
        try:
            assert len(principal.calendars()) == initial_count + 1
        finally:
            cal.delete()

    def test_move_event_between_calendars(self, caldav_client):
        """Create event in cal1, move to cal2, verify in cal2 only."""
        principal = caldav_client.principal()
        cal1 = principal.make_calendar(name="move-src-cal")
        cal2 = principal.make_calendar(name="move-dst-cal")
        try:
            # Create event in cal1
            ev = Event()
            ev.add("uid", "move-event@test")
            ev.add("summary", "Event to Move")
            ev.add("dtstart", datetime(2026, 4, 1, 10, 0))
            ev.add("dtend", datetime(2026, 4, 1, 11, 0))
            c = Calendar()
            c.add_component(ev)
            cal1.save_event(c.to_ical())

            # Read from cal1, create in cal2, delete from cal1
            original = cal1.event_by_uid("move-event@test")
            ical_data = original.data

            cal2.save_event(ical_data)
            original.delete()

            # Verify: not in cal1, exists in cal2
            cal1_events = cal1.events()
            cal2_events = cal2.events()
            assert len(cal1_events) == 0
            assert len(cal2_events) == 1
        finally:
            cal1.delete()
            cal2.delete()
