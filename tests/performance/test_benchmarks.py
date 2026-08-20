"""Performance benchmarks for critical CalDAV operations.

Run with: python3 -m pytest tests/performance/test_benchmarks.py --benchmark-only

Requires: pip install pytest-benchmark
"""

import pytest

try:
    import pytest_benchmark  # noqa: F401
    HAS_BENCHMARK = True
except ImportError:
    HAS_BENCHMARK = False

from conftest import FakeCalendar, FakeEvent, make_event, patch_caldav
import server

pytestmark = [
    pytest.mark.performance,
    pytest.mark.skipif(not HAS_BENCHMARK, reason="pytest-benchmark not installed"),
]


class TestEventCreationBenchmarks:
    """Benchmarks for caldav_create_event."""

    def test_benchmark_create_simple_event(self, benchmark):
        """Benchmark creating a minimal event."""
        fake_cal = FakeCalendar()
        patchers = patch_caldav(fake_cal)

        def run():
            server.caldav_create_event(
                summary="Benchmark Event",
                start="2026-01-01T10:00:00Z",
                end="2026-01-01T11:00:00Z",
            )

        try:
            benchmark(run)
        finally:
            for p in patchers:
                p.stop()

    def test_benchmark_create_event_with_attendees(self, benchmark):
        """Benchmark creating an event with multiple attendees."""
        fake_cal = FakeCalendar()
        patchers = patch_caldav(fake_cal)

        def run():
            server.caldav_create_event(
                summary="Meeting",
                start="2026-01-01T10:00:00Z",
                end="2026-01-01T11:00:00Z",
                attendees="alice@example.com, bob@example.com, carol@example.com",
            )

        try:
            benchmark(run)
        finally:
            for p in patchers:
                p.stop()

    def test_benchmark_create_event_with_recurrence(self, benchmark):
        """Benchmark creating a recurring event with RRULE."""
        fake_cal = FakeCalendar()
        patchers = patch_caldav(fake_cal)

        def run():
            server.caldav_create_event(
                summary="Weekly Standup",
                start="2026-01-01T10:00:00Z",
                end="2026-01-01T10:30:00Z",
                rrule="FREQ=WEEKLY;COUNT=10",
            )

        try:
            benchmark(run)
        finally:
            for p in patchers:
                p.stop()


class TestEventRetrievalBenchmarks:
    """Benchmarks for caldav_get_events and caldav_search_events."""

    def test_benchmark_get_events(self, benchmark):
        """Benchmark retrieving events from a calendar with multiple events."""
        events = [
            FakeEvent(
                make_event(uid=f"ev{i}@bench", summary=f"Event {i}"),
            )
            for i in range(50)
        ]
        fake_cal = FakeCalendar(events=events)
        patchers = patch_caldav(fake_cal)

        def run():
            server.caldav_get_events()

        try:
            benchmark(run)
        finally:
            for p in patchers:
                p.stop()

    def test_benchmark_search_events(self, benchmark):
        """Benchmark searching events by query across a populated calendar."""
        events = [
            FakeEvent(
                make_event(uid=f"ev{i}@bench", summary=f"Meeting #{i}"),
            )
            for i in range(50)
        ]
        fake_cal = FakeCalendar(events=events)
        patchers = patch_caldav(fake_cal)

        def run():
            server.caldav_search_events(query="Meeting")

        try:
            benchmark(run)
        finally:
            for p in patchers:
                p.stop()


class TestSerializationBenchmarks:
    """Benchmarks for _event_to_dict serialization."""

    def test_benchmark_event_to_dict(self, benchmark):
        """Benchmark serializing an event component to dict."""
        ev = FakeEvent(make_event(attendees=["a@ex.com", "b@ex.com"]))
        benchmark(server._event_to_dict, ev)

    def test_benchmark_event_to_dict_no_attendees(self, benchmark):
        """Benchmark serializing an event without attendees."""
        ev = FakeEvent(make_event())
        benchmark(server._event_to_dict, ev)
