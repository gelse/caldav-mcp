"""Integration tests: full CRUD lifecycle against a live Radicale server.

These tests require Radicale running via:
    docker compose -f docker-compose.test.yaml up -d
"""

from datetime import datetime

import pytest
from icalendar import Calendar, Event

pytestmark = [pytest.mark.integration, pytest.mark.slow]


class TestCalendarCRUD:
    """Create / Read / Delete lifecycle for calendars."""

    def test_create_calendar(self, caldav_client):
        """Create a new calendar and verify it appears in the list."""
        principal = caldav_client.principal()
        cal = principal.make_calendar(name="lifecycle-create-test")
        calendars = principal.calendars()
        names = [c.name for c in calendars]
        assert "lifecycle-create-test" in names
        cal.delete()

    def test_delete_calendar(self, caldav_client):
        """Create and then delete a calendar."""
        principal = caldav_client.principal()
        cal = principal.make_calendar(name="lifecycle-delete-test")
        cal.delete()
        calendars = principal.calendars()
        names = [c.name for c in calendars]
        assert "lifecycle-delete-test" not in names


class TestEventCRUD:
    """Create / Read / Update / Delete lifecycle for events."""

    def test_create_event(self, test_calendar):
        """Create an event and verify it can be retrieved."""
        ev = Event()
        ev.add("uid", "crud-create@test")
        ev.add("summary", "CRUD Create Test")
        ev.add("dtstart", datetime(2026, 6, 1, 10, 0))
        ev.add("dtend", datetime(2026, 6, 1, 11, 0))
        cal = Calendar()
        cal.add_component(ev)
        test_calendar.save_event(cal.to_ical())

        found = test_calendar.event_by_uid("crud-create@test")
        assert found is not None
        assert "CRUD Create Test" in str(found.icalendar_component)

    def test_read_event(self, populated_calendar):
        """Read an event from a pre-populated calendar."""
        event = populated_calendar.event_by_uid("test-event-0@integration-test")
        assert event is not None
        comp = event.icalendar_component
        assert str(comp.get("summary")) == "Test Event 0"

    def test_update_event(self, populated_calendar):
        """Update an event's summary."""
        event = populated_calendar.event_by_uid("test-event-0@integration-test")
        comp = event.icalendar_component
        comp["summary"] = "Updated Event 0"
        event.save()
        # Re-fetch and verify
        refetched = populated_calendar.event_by_uid("test-event-0@integration-test")
        assert str(refetched.icalendar_component.get("summary")) == "Updated Event 0"

    def test_delete_event(self, populated_calendar):
        """Delete an event from a pre-populated calendar."""
        event = populated_calendar.event_by_uid("test-event-1@integration-test")
        event.delete()
        # Verify deletion
        events = populated_calendar.events()
        uids = [str(e.icalendar_component.get("uid")) for e in events]
        assert "test-event-1@integration-test" not in uids

    def test_list_events(self, populated_calendar):
        """List all events in a pre-populated calendar."""
        events = populated_calendar.events()
        assert len(events) == 3
