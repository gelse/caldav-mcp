"""Bounded LRU cache for DAVClient instances.

Each MCP tool invocation previously created a brand-new ``DAVClient`` (and
therefore a fresh ``requests.Session``), negating HTTP keep-alive and adding
latency.  This module provides a thread-safe, bounded cache that reuses
``DAVClient`` instances keyed by ``(url, username)``.

Design decisions
----------------
* **Key**: ``(url, username)`` — the password is **never** used as a cache key
  or stored in the cache metadata.
* **Password verification**: For passthrough clients where the caller provides
  credentials per request, :meth:`~get_with_password` stores a keyed hash of
  the password and verifies it on cache hits to prevent cross-user escalation.
  Direct-credential clients use the plain :meth:`~get` which skips verification
  (the password does not change between MCP users in that mode).
* **Eviction**: LRU with configurable *max_size* and TTL.
* **Thread-safety**: All public methods are serialised with a
  ``threading.Lock``.
* **Cleanup**: Evicted / expired clients have ``close()`` called so the
  underlying HTTP session is properly released.
"""

from __future__ import annotations

import hmac
import logging
import threading
import time
from collections import OrderedDict

from caldav_mcp.types import CalDAVClient

log = logging.getLogger(__name__)

# Default configuration – intentionally conservative.
DEFAULT_MAX_SIZE = 8
DEFAULT_TTL_SECONDS = 3600  # 1 hour

# Server-side secret for password hashing.  Randomised once per process;
# cache is in-memory only so this is appropriate for cross-user protection.
_PASSWORD_SECRET = b"caldav-mcp-passthrough-verify-" + __import__("os").urandom(16).hex().encode()


def _hash_password(password: str) -> str:
    """Return a hex HMAC-SHA256 keyed hash of *password*."""
    return hmac.new(_PASSWORD_SECRET, password.encode("utf-8"), "sha256").hexdigest()


class ClientCache:
    """Thread-safe bounded LRU cache for ``DAVClient`` instances."""

    def __init__(
        self,
        max_size: int = DEFAULT_MAX_SIZE,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._max_size = max_size
        self._ttl = ttl_seconds
        # OrderedDict gives us O(1) move_to_end / popitem.
        self._cache: OrderedDict[tuple[str, str], CalDAVClient] = OrderedDict()
        self._timestamps: dict[tuple[str, str], float] = {}
        self._pw_hashes: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_internal(self, key: tuple[str, str]) -> CalDAVClient | None:
        """Return a cached ``DAVClient`` if one exists and is still alive.

        Must be called with ``self._lock`` held.  Returns ``None`` on cache
        miss or TTL expiry (the expired entry is closed and removed
        automatically).
        """
        client = self._cache.get(key)
        if client is None:
            return None

        # TTL check
        if time.monotonic() - self._timestamps[key] >= self._ttl:
            log.debug("Cache TTL expired for %s", key)
            del self._cache[key]
            del self._timestamps[key]
            self._pw_hashes.pop(key, None)
            try:
                client.close()
            except Exception:
                log.debug("Error closing expired client", exc_info=True)
            return None

        # Touch for LRU ordering
        self._cache.move_to_end(key)
        return client

    def _evict_lru(self) -> None:
        """Evict the least-recently-used entry while at or above capacity.

        Must be called with ``self._lock`` held.
        """
        while len(self._cache) >= self._max_size:
            evict_key, evict_client = self._cache.popitem(last=False)
            del self._timestamps[evict_key]
            self._pw_hashes.pop(evict_key, None)
            log.debug("Evicted cache entry %s", evict_key)
            try:
                evict_client.close()
            except Exception:
                log.debug("Error closing evicted client", exc_info=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, url: str, username: str) -> CalDAVClient | None:
        """Return a cached ``DAVClient`` if one exists and is still alive.

        Returns ``None`` on cache miss or TTL expiry (the expired entry is
        closed and removed automatically).  Does **not** verify the password
        — use :meth:`get_with_password` for passthrough clients.
        """
        with self._lock:
            return self._get_internal((url, username))

    def get_with_password(self, url: str, username: str, password: str) -> CalDAVClient | None:
        """Return a cached client only if the supplied password matches.

        For passthrough clients the caller provides credentials per request;
        a keyed hash of the password is stored alongside the client and
        verified on each cache hit to prevent cross-user escalation.  On
        mismatch the stale entry is evicted so a fresh client will be built.
        """
        key = (url, username)
        with self._lock:
            client = self._get_internal(key)
            if client is None:
                return None

            stored_hash = self._pw_hashes.get(key)
            if stored_hash is None:
                # Legacy entry without a stored hash — treat as a miss.
                return None

            supplied_hash = _hash_password(password)
            if not hmac.compare_digest(supplied_hash, stored_hash):
                log.debug("Password mismatch for cache key %s — evicting", key)
                del self._cache[key]
                del self._timestamps[key]
                del self._pw_hashes[key]
                try:
                    client.close()
                except Exception:
                    log.debug("Error closing mismatched client", exc_info=True)
                return None

            return client

    def put(
        self,
        url: str,
        username: str,
        client: CalDAVClient,
        password: str | None = None,
    ) -> None:
        """Insert or replace a ``DAVClient`` in the cache.

        If the cache is at capacity the least-recently-used entry is evicted
        (and its ``close()`` called).  When *password* is provided a keyed
        hash is stored for later verification by :meth:`get_with_password`.
        """
        key = (url, username)
        with self._lock:
            # If replacing an existing entry, close the old one first.
            if key in self._cache:
                old = self._cache.pop(key)
                del self._timestamps[key]
                self._pw_hashes.pop(key, None)
                try:
                    old.close()
                except Exception:
                    log.debug("Error closing replaced client", exc_info=True)

            # Evict LRU entries while at or above capacity.
            self._evict_lru()

            self._cache[key] = client
            self._timestamps[key] = time.monotonic()
            if password is not None:
                self._pw_hashes[key] = _hash_password(password)

    def clear(self) -> None:
        """Close and remove **all** cached clients."""
        with self._lock:
            for client in self._cache.values():
                try:
                    client.close()
                except Exception:
                    log.debug("Error closing client on clear", exc_info=True)
            self._cache.clear()
            self._timestamps.clear()
            self._pw_hashes.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


# ------------------------------------------------------------------
# Module-level singleton with injectable accessor.
# ------------------------------------------------------------------
_default_cache = ClientCache()


def get_cache() -> ClientCache:
    """Return the active cache instance."""
    return _default_cache


def set_cache(cache: ClientCache) -> None:
    """Replace the active cache instance (useful for testing)."""
    global _default_cache
    _default_cache = cache


def __getattr__(name: str):
    """Allow ``from caldav_mcp.client_cache import client_cache`` to keep
    working by dynamically resolving to the current ``_default_cache``."""
    if name == "client_cache":
        return _default_cache
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
