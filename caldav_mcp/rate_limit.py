"""Token-bucket rate limiter for authentication attempts.

Limits failed auth attempts per source identifier (e.g. client IP)
to 10 per minute with exponential backoff.  The limiter is in-memory
and resets on server restart — acceptable for a single-instance MCP
server.
"""

import threading
import time
from collections import defaultdict

from caldav_mcp.config import RATE_LIMIT_MAX_FAILURES, RATE_LIMIT_WINDOW_SECONDS

_DEFAULT_MAX_FAILURES = 10
_DEFAULT_WINDOW_SECONDS = 60
_BACKOFF_BASE_SECONDS = 5


class RateLimiter:
    """Thread-safe rate limiter using a sliding window counter."""

    def __init__(
        self,
        max_failures: int = _DEFAULT_MAX_FAILURES,
        window_seconds: int = _DEFAULT_WINDOW_SECONDS,
    ) -> None:
        self._max_failures = max_failures
        self._window = window_seconds
        self._failures: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def record_failure(self, key: str) -> None:
        """Record a failed attempt for the given key."""
        now = time.monotonic()
        with self._lock:
            self._failures[key].append(now)
            # Prune old entries outside the window.
            self._failures[key] = [
                t for t in self._failures[key] if now - t < self._window
            ]

    def is_rate_limited(self, key: str) -> bool:
        """Return True if the key has exceeded the failure threshold."""
        now = time.monotonic()
        with self._lock:
            self._failures[key] = [
                t for t in self._failures[key] if now - t < self._window
            ]
            return len(self._failures[key]) >= self._max_failures

    def get_backoff_seconds(self, key: str) -> int:
        """Return the number of seconds to wait before allowing another attempt.

        Returns 0 when not rate-limited.  Uses exponential backoff based on
        the number of recent failures.
        """
        now = time.monotonic()
        with self._lock:
            count = len([
                t for t in self._failures[key] if now - t < self._window
            ])
        if count < self._max_failures:
            return 0
        # Exponential backoff: 5s, 10s, 20s, ... capped at 5 minutes
        power = min(count - self._max_failures, 6)
        return min(_BACKOFF_BASE_SECONDS * (2 ** power), 300)

    def reset(self, key: str) -> None:
        """Clear failure history for a key (e.g. after successful auth)."""
        with self._lock:
            self._failures.pop(key, None)


# Module-level singleton
auth_rate_limiter = RateLimiter(
    max_failures=RATE_LIMIT_MAX_FAILURES,
    window_seconds=RATE_LIMIT_WINDOW_SECONDS,
)
