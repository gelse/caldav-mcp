"""Fixtures for performance and benchmark tests."""

import sys
import tracemalloc
from pathlib import Path

import pytest

# Make the root ``tests/`` directory importable so ``conftest`` resolves.
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

# Re-export shared helpers so ``from conftest import ...`` works.
from conftest import (  # noqa: F401, E402
    FakeCalendar,
    FakeEvent,
    make_event,
    patch_caldav,
)


@pytest.fixture(autouse=True)
def _reset_client_cache():
    """Clear the client cache before each benchmark to avoid cross-test contamination."""
    from caldav_mcp.client_cache import get_cache

    get_cache().clear()
    yield
    get_cache().clear()


@pytest.fixture()
def trace_memory():
    """Context manager that traces memory allocation via tracemalloc.

    Usage in test:
        snapshot = trace_memory.take_snapshot()
        # ... do work ...
        diff = snapshot.compare_to(trace_memory.take_snapshot(), 'lineno')
    """
    tracemalloc.start()
    yield tracemalloc
    tracemalloc.stop()
