"""Fixtures for CalDAV integration tests.

Requires Radicale running via:
    docker compose -f docker-compose.test.yaml up -d

The RADICALE_URL defaults to http://localhost:5232/testuser/
"""

import os
import time

import pytest
from caldav import DAVClient

RADICALE_URL = os.environ.get("RADICALE_URL", "http://localhost:5232")
RADICALE_USER = os.environ.get("RADICALE_USER", "testuser")
RADICALE_PASS = os.environ.get("RADICALE_PASS", "testpass")


@pytest.fixture(scope="session")
def radicale_url():
    """Base URL of the Radicale server."""
    return RADICALE_URL


@pytest.fixture(scope="session")
def caldav_client(radicale_url):
    """A real DAVClient connected to the Radicale test server.

    Session-scoped to avoid reconnecting for every test.
    """
    client = DAVClient(
        url=radicale_url,
        username=RADICALE_USER,
        password=RADICALE_PASS,
    )
    yield client
    # No explicit close needed; session cleanup handles it.


@pytest.fixture()
def test_calendar(caldav_client):
    """Create a fresh test calendar and clean up after the test.

    Yields the caldav.Calendar object.
    """
    principal = caldav_client.principal()
    cal_name = f"integration-test-{int(time.time() * 1000)}"
    cal = principal.make_calendar(name=cal_name)
    yield cal
    # Cleanup: delete calendar and all its events
    try:
        for event in cal.events():
            event.delete()
        cal.delete()
    except Exception:
        pass  # Best-effort cleanup


@pytest.fixture()
def populated_calendar(test_calendar):
    """A test calendar pre-populated with sample events."""
    from datetime import datetime

    from icalendar import Calendar, Event

    for i in range(3):
        ev = Event()
        ev.add("uid", f"test-event-{i}@integration-test")
        ev.add("summary", f"Test Event {i}")
        ev.add("dtstart", datetime(2026, 1, 15, 10 + i, 0))
        ev.add("dtend", datetime(2026, 1, 15, 11 + i, 0))
        cal = Calendar()
        cal.add_component(ev)
        test_calendar.save_event(cal.to_ical())
    return test_calendar
