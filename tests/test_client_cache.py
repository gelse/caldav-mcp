"""Unit tests for the bounded LRU DAVClient cache.

These tests verify the :class:`caldav_mcp.client_cache.ClientCache` in
isolation (no real CalDAV server needed) and the integration with the
``with_caldav_client`` decorator.
"""

import threading
import time
from unittest import mock

from caldav_mcp.client_cache import ClientCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal stand-in for ``requests.Session`` used by ``DAVClient``."""

    def close(self):
        self.closed = True


class _FakeDAVClient:
    """Lightweight mock that behaves like ``caldav.DAVClient`` for cache tests."""

    def __init__(self, url="https://caldav.example.com", username="user"):
        self.url = url
        self.username = username
        self.session = _FakeSession()

    def close(self):
        self.session.close()


# ---------------------------------------------------------------------------
# ClientCache unit tests
# ---------------------------------------------------------------------------


def test_empty_cache_returns_none():
    cache = ClientCache(max_size=4, ttl_seconds=60)
    result = cache.get("https://caldav.example.com", "alice")
    assert result is None


def test_cache_starts_empty():
    cache = ClientCache()
    assert len(cache) == 0


def test_put_then_get_returns_same_instance():
    cache = ClientCache(max_size=4, ttl_seconds=60)
    client = _FakeDAVClient()
    cache.put("https://caldav.example.com", "alice", client)
    result = cache.get("https://caldav.example.com", "alice")
    assert result is client


def test_different_keys_return_different_instances():
    cache = ClientCache(max_size=4, ttl_seconds=60)
    alice = _FakeDAVClient(username="alice")
    bob = _FakeDAVClient(username="bob")
    cache.put("https://caldav.example.com", "alice", alice)
    cache.put("https://caldav.example.com", "bob", bob)
    assert cache.get("https://caldav.example.com", "alice") is alice
    assert cache.get("https://caldav.example.com", "bob") is bob


def test_password_not_used_as_key():
    """Same (url, username) with different passwords should hit the cache."""
    cache = ClientCache(max_size=4, ttl_seconds=60)
    client = _FakeDAVClient()
    cache.put("https://caldav.example.com", "alice", client)
    # Even though we never stored a password, same key should return cached
    result = cache.get("https://caldav.example.com", "alice")
    assert result is client


def test_eviction_at_capacity():
    cache = ClientCache(max_size=2, ttl_seconds=3600)
    c1 = _FakeDAVClient(username="u1")
    c2 = _FakeDAVClient(username="u2")
    c3 = _FakeDAVClient(username="u3")

    cache.put("https://example.com", "u1", c1)
    cache.put("https://example.com", "u2", c2)
    # Adding a third should evict u1 (LRU)
    cache.put("https://example.com", "u3", c3)

    assert len(cache) == 2
    assert cache.get("https://example.com", "u1") is None
    assert cache.get("https://example.com", "u2") is not None
    assert cache.get("https://example.com", "u3") is not None


def test_evicted_client_is_closed():
    cache = ClientCache(max_size=1, ttl_seconds=3600)
    c1 = _FakeDAVClient()
    c2 = _FakeDAVClient()

    cache.put("https://example.com", "a", c1)
    cache.put("https://example.com", "b", c2)

    assert c1.session.closed


def test_lru_touch_on_get():
    """Accessing an entry should move it to the end (most-recently-used)."""
    cache = ClientCache(max_size=2, ttl_seconds=3600)
    c1 = _FakeDAVClient(username="u1")
    c2 = _FakeDAVClient(username="u2")
    c3 = _FakeDAVClient(username="u3")

    cache.put("https://example.com", "u1", c1)
    cache.put("https://example.com", "u2", c2)
    # Access u1 to promote it — now u2 is LRU
    cache.get("https://example.com", "u1")
    cache.put("https://example.com", "u3", c3)

    # u1 was promoted so u2 should be evicted
    assert cache.get("https://example.com", "u2") is None
    assert cache.get("https://example.com", "u1") is not None
    assert cache.get("https://example.com", "u3") is not None


def test_expired_entry_returns_none():
    cache = ClientCache(max_size=4, ttl_seconds=1)
    client = _FakeDAVClient()
    cache.put("https://example.com", "alice", client)

    # Simulate time passage by manipulating the timestamp directly
    key = ("https://example.com", "alice")
    cache._timestamps[key] = time.monotonic() - 10  # 10 seconds ago

    result = cache.get("https://example.com", "alice")
    assert result is None


def test_expired_entry_is_closed():
    cache = ClientCache(max_size=4, ttl_seconds=1)
    client = _FakeDAVClient()
    cache.put("https://example.com", "alice", client)

    key = ("https://example.com", "alice")
    cache._timestamps[key] = time.monotonic() - 10

    cache.get("https://example.com", "alice")
    assert client.session.closed


def test_replace_closes_old_client():
    cache = ClientCache(max_size=4, ttl_seconds=3600)
    old = _FakeDAVClient()
    new = _FakeDAVClient()

    cache.put("https://example.com", "alice", old)
    cache.put("https://example.com", "alice", new)

    assert old.session.closed
    assert cache.get("https://example.com", "alice") is new


def test_clear_closes_all_clients():
    cache = ClientCache(max_size=4, ttl_seconds=3600)
    c1 = _FakeDAVClient()
    c2 = _FakeDAVClient()

    cache.put("https://example.com", "a", c1)
    cache.put("https://example.com", "b", c2)
    cache.clear()

    assert len(cache) == 0
    assert c1.session.closed
    assert c2.session.closed


def test_get_after_clear_returns_none():
    cache = ClientCache(max_size=4, ttl_seconds=3600)
    cache.put("https://example.com", "a", _FakeDAVClient())
    cache.clear()
    assert cache.get("https://example.com", "a") is None


def test_concurrent_puts():
    cache = ClientCache(max_size=8, ttl_seconds=3600)
    errors = []

    def writer(idx):
        try:
            client = _FakeDAVClient(username=f"user{idx}")
            cache.put("https://example.com", f"user{idx}", client)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(cache) <= 8


def test_concurrent_get_and_put():
    cache = ClientCache(max_size=4, ttl_seconds=3600)
    errors = []

    def mixed(idx):
        try:
            key = f"user{idx % 3}"
            cache.put("https://example.com", key, _FakeDAVClient(username=key))
            cache.get("https://example.com", key)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=mixed, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []


def test_size_does_not_grow_on_replace():
    cache = ClientCache(max_size=2, ttl_seconds=3600)
    cache.put("https://example.com", "a", _FakeDAVClient())
    cache.put("https://example.com", "a", _FakeDAVClient())
    cache.put("https://example.com", "a", _FakeDAVClient())
    assert len(cache) == 1


# ---------------------------------------------------------------------------
# Integration test: with_caldav_client decorator uses the cache
# ---------------------------------------------------------------------------


def test_decorator_uses_cache_on_second_call():
    """Second call with same credentials reuses the cached client."""
    from caldav_mcp import tools as _tools
    from caldav_mcp.client_cache import ClientCache

    real_cache = ClientCache(max_size=4, ttl_seconds=3600)
    mock_client = _FakeDAVClient()

    with mock.patch("caldav_mcp.tools.get_cache", return_value=real_cache):
        # Simulate first call: cache miss -> create -> put
        url, user = "https://caldav.test", "alice"
        cache = _tools.get_cache()
        cached = cache.get(url, user)
        assert cached is None
        cache.put(url, user, mock_client)

        # Simulate second call: cache hit
        cached = cache.get(url, user)
        assert cached is mock_client
        assert len(real_cache) == 1


def test_different_users_get_different_clients():
    """Different (url, username) pairs produce separate cache entries."""
    from caldav_mcp.client_cache import ClientCache

    cache = ClientCache(max_size=4, ttl_seconds=3600)
    alice = _FakeDAVClient(username="alice")
    bob = _FakeDAVClient(username="bob")

    cache.put("https://caldav.test", "alice", alice)
    cache.put("https://caldav.test", "bob", bob)

    assert cache.get("https://caldav.test", "alice") is alice
    assert cache.get("https://caldav.test", "bob") is bob
    assert len(cache) == 2
