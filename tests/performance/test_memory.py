"""Memory profiling tests using tracemalloc.

These tests verify that critical operations don't leak memory
and that the client cache properly cleans up.

Run with: python3 -m pytest tests/performance/test_memory.py -v
"""

import tracemalloc

import pytest

from caldav_mcp.client_cache import ClientCache

pytestmark = [pytest.mark.performance]


class _MockClient:
    """Minimal mock client with a close() method for cache tests."""

    def close(self):
        pass


def _make_mock_client():
    """Create a mock client suitable for cache insertion."""
    return _MockClient()


class TestClientCacheMemory:
    """Verify the client cache doesn't leak memory on eviction."""

    def test_cache_eviction_releases_memory(self):
        """Evicted clients should be garbage collected."""
        tracemalloc.start()

        cache = ClientCache(max_size=5, ttl_seconds=3600)

        snapshot1 = tracemalloc.take_snapshot()

        # Fill cache beyond capacity to trigger evictions
        for i in range(20):
            cache.put(f"https://host{i}.example.com", "user", _make_mock_client())

        snapshot2 = tracemalloc.take_snapshot()
        diff = snapshot2.compare_to(snapshot1, "lineno")

        # Total memory growth should be bounded (not proportional to 20 clients)
        total_growth = sum(stat.size_diff for stat in diff if stat.size_diff > 0)
        # Assert growth is reasonable (< 1MB for mock objects)
        assert total_growth < 1_000_000, f"Excessive memory growth: {total_growth} bytes"

        tracemalloc.stop()

    def test_cache_clear_releases_memory(self):
        """Clearing the cache should release all held references."""
        tracemalloc.start()

        cache = ClientCache(max_size=20, ttl_seconds=3600)

        snapshot1 = tracemalloc.take_snapshot()

        # Fill cache
        for i in range(20):
            cache.put(f"https://host{i}.example.com", "user", _make_mock_client())

        assert len(cache) == 20

        # Clear all entries
        cache.clear()
        assert len(cache) == 0

        snapshot2 = tracemalloc.take_snapshot()
        diff = snapshot2.compare_to(snapshot1, "lineno")

        # After clearing, net growth should be minimal
        total_growth = sum(stat.size_diff for stat in diff if stat.size_diff > 0)
        assert total_growth < 500_000, f"Memory not released after clear: {total_growth} bytes"

        tracemalloc.stop()

    def test_cache_ttl_expiry_releases_memory(self):
        """Expired entries should be cleaned up and not accumulate."""
        tracemalloc.start()

        # TTL of 0 means everything is expired immediately
        cache = ClientCache(max_size=20, ttl_seconds=0)

        snapshot1 = tracemalloc.take_snapshot()

        # Insert and immediately expire
        for i in range(10):
            cache.put(f"https://host{i}.example.com", "user", _make_mock_client())
            # Each get will find TTL expired and remove the entry
            cache.get(f"https://host{i}.example.com", "user")

        snapshot2 = tracemalloc.take_snapshot()
        diff = snapshot2.compare_to(snapshot1, "lineno")

        total_growth = sum(stat.size_diff for stat in diff if stat.size_diff > 0)
        assert total_growth < 500_000, f"Memory leak with TTL expiry: {total_growth} bytes"

        tracemalloc.stop()


class TestEventSerializationMemory:
    """Verify event serialization doesn't leak memory."""

    def test_event_to_dict_repeated_serialization(self):
        """Serialize many events and verify no memory accumulation."""
        from conftest import FakeEvent, make_event

        import server

        tracemalloc.start()

        snapshot1 = tracemalloc.take_snapshot()

        ev = FakeEvent(make_event(attendees=["a@ex.com", "b@ex.com"]))
        for _ in range(1000):
            server._event_to_dict(ev)

        snapshot2 = tracemalloc.take_snapshot()
        diff = snapshot2.compare_to(snapshot1, "lineno")

        total_growth = sum(stat.size_diff for stat in diff if stat.size_diff > 0)
        assert total_growth < 500_000, f"Memory grew during serialization: {total_growth} bytes"

        tracemalloc.stop()
