"""Integration tests: concurrent access patterns."""

import threading
from datetime import datetime

import pytest
from icalendar import Calendar, Event

pytestmark = [pytest.mark.integration, pytest.mark.slow]


class TestConcurrentAccess:
    """Tests for concurrent read/write operations."""

    def test_concurrent_event_creation(self, test_calendar):
        """Multiple threads creating events simultaneously."""
        errors = []

        def create_event(idx):
            try:
                ev = Event()
                ev.add("uid", f"concurrent-{idx}@test")
                ev.add("summary", f"Concurrent Event {idx}")
                ev.add("dtstart", datetime(2026, 7, 1, idx, 0))
                ev.add("dtend", datetime(2026, 7, 1, idx + 1, 0))
                cal = Calendar()
                cal.add_component(ev)
                test_calendar.save_event(cal.to_ical())
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=create_event, args=(i,)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Errors during concurrent creation: {errors}"
        assert len(test_calendar.events()) == 10

    def test_concurrent_event_read_write(self, test_calendar):
        """Simultaneous reads and writes to the same calendar."""
        from icalendar import Calendar, Event

        # Seed initial events
        for i in range(5):
            ev = Event()
            ev.add("uid", f"rw-initial-{i}@test")
            ev.add("summary", f"Initial Event {i}")
            ev.add("dtstart", datetime(2026, 7, 2, i, 0))
            ev.add("dtend", datetime(2026, 7, 2, i + 1, 0))
            cal = Calendar()
            cal.add_component(ev)
            test_calendar.save_event(cal.to_ical())

        read_results = []
        write_errors = []
        read_done = threading.Event()
        write_done = threading.Event()

        def reader():
            while not write_done.is_set():
                try:
                    events = test_calendar.events()
                    read_results.append(len(events))
                except Exception:
                    pass
            # Final read after writes complete
            try:
                events = test_calendar.events()
                read_results.append(len(events))
            except Exception:
                pass

        def writer(idx):
            try:
                ev = Event()
                ev.add("uid", f"rw-write-{idx}@test")
                ev.add("summary", f"Written Event {idx}")
                ev.add("dtstart", datetime(2026, 7, 3, idx, 0))
                ev.add("dtend", datetime(2026, 7, 3, idx + 1, 0))
                cal = Calendar()
                cal.add_component(ev)
                test_calendar.save_event(cal.to_ical())
            except Exception as e:
                write_errors.append(e)

        reader_thread = threading.Thread(target=reader)
        writer_threads = [
            threading.Thread(target=writer, args=(i,)) for i in range(5)
        ]

        reader_thread.start()
        for t in writer_threads:
            t.start()
        for t in writer_threads:
            t.join()
        write_done.set()
        reader_thread.join()

        assert write_errors == [], f"Write errors: {write_errors}"
        # Reader should have seen at least the initial 5 events
        assert any(r >= 5 for r in read_results)
        # Final count should be 5 initial + 5 written = 10
        assert len(test_calendar.events()) == 10
