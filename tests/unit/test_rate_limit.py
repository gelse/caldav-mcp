"""Unit tests for caldav_mcp.rate_limit — sliding-window rate limiter."""

import time

from caldav_mcp.config import RATE_LIMIT_MAX_FAILURES, RATE_LIMIT_WINDOW_SECONDS
from caldav_mcp.rate_limit import RateLimiter, admin_rate_limiter, auth_rate_limiter


def _make_limiter(max_failures=10, window_seconds=60):
    """Create a fresh RateLimiter for isolated testing."""
    return RateLimiter(max_failures=max_failures, window_seconds=window_seconds)


# ---------------------------------------------------------------------------
# Basic allow / block behaviour
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_within_limit():
    """9 failures should NOT trigger rate limiting (threshold is 10)."""
    limiter = _make_limiter(max_failures=10)
    for _ in range(9):
        limiter.record_failure("ip-1")
    assert limiter.is_rate_limited("ip-1") is False


def test_rate_limiter_blocks_at_limit():
    """10 failures should trigger rate limiting."""
    limiter = _make_limiter(max_failures=10)
    for _ in range(10):
        limiter.record_failure("ip-2")
    assert limiter.is_rate_limited("ip-2") is True


def test_rate_limiter_blocks_after_limit():
    """After exceeding the limit, is_rate_limited continues to return True."""
    limiter = _make_limiter(max_failures=5)
    for _ in range(6):
        limiter.record_failure("ip-3")
    assert limiter.is_rate_limited("ip-3") is True


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_rate_limiter_reset_clears_history():
    """After reset, the key is no longer rate-limited."""
    limiter = _make_limiter(max_failures=3)
    for _ in range(5):
        limiter.record_failure("ip-4")
    assert limiter.is_rate_limited("ip-4") is True
    limiter.reset("ip-4")
    assert limiter.is_rate_limited("ip-4") is False


def test_rate_limiter_reset_nonexistent_key():
    """Resetting a key that was never used should not raise."""
    limiter = _make_limiter()
    limiter.reset("nonexistent")


# ---------------------------------------------------------------------------
# Window expiry
# ---------------------------------------------------------------------------


def test_rate_limiter_window_expiry():
    """Failures older than the window are pruned."""
    limiter = _make_limiter(max_failures=3, window_seconds=1)
    # Record 3 failures immediately
    for _ in range(3):
        limiter.record_failure("ip-5")
    assert limiter.is_rate_limited("ip-5") is True

    # Wait for window to expire
    time.sleep(1.1)

    # Old failures should be pruned — no longer rate-limited
    assert limiter.is_rate_limited("ip-5") is False


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


def test_rate_limiter_backoff_zero_within_limit():
    """Backoff is 0 when not rate-limited."""
    limiter = _make_limiter(max_failures=10)
    for _ in range(5):
        limiter.record_failure("ip-6")
    assert limiter.get_backoff_seconds("ip-6") == 0


def test_rate_limiter_backoff_increases():
    """Exponential backoff grows with more failures beyond the limit."""
    limiter = _make_limiter(max_failures=3, window_seconds=300)
    for _ in range(3):
        limiter.record_failure("ip-7")

    # Exactly at limit: base backoff = 5s
    backoff_3 = limiter.get_backoff_seconds("ip-7")
    assert backoff_3 >= 5

    # Record more failures to increase backoff
    for _ in range(2):
        limiter.record_failure("ip-7")

    backoff_5 = limiter.get_backoff_seconds("ip-7")
    assert backoff_5 > backoff_3


def test_rate_limiter_backoff_capped_at_5_minutes():
    """Backoff never exceeds 300 seconds (5 minutes)."""
    limiter = _make_limiter(max_failures=2, window_seconds=600)
    # Record many failures to trigger high backoff
    for _ in range(20):
        limiter.record_failure("ip-8")
    assert limiter.get_backoff_seconds("ip-8") <= 300


# ---------------------------------------------------------------------------
# Independent keys
# ---------------------------------------------------------------------------


def test_rate_limiter_independent_keys():
    """Rate limiting is per-key; different keys are independent."""
    limiter = _make_limiter(max_failures=3)
    for _ in range(5):
        limiter.record_failure("ip-a")
    assert limiter.is_rate_limited("ip-a") is True
    assert limiter.is_rate_limited("ip-b") is False


# ---------------------------------------------------------------------------
# New key defaults
# ---------------------------------------------------------------------------


def test_rate_limiter_new_key_not_limited():
    """A key with no recorded failures is never rate-limited."""
    limiter = _make_limiter()
    assert limiter.is_rate_limited("brand-new-key") is False
    assert limiter.get_backoff_seconds("brand-new-key") == 0


# ---------------------------------------------------------------------------
# Admin vs auth singletons (issue-16 milestone M2.2)
# ---------------------------------------------------------------------------


def test_admin_and_auth_rate_limiters_are_distinct_instances():
    """admin_rate_limiter and auth_rate_limiter are separate objects."""
    assert admin_rate_limiter is not auth_rate_limiter


def test_admin_rate_limiter_buckets_independent_from_auth():
    """Exhausting the admin bucket leaves the auth bucket untouched (same key)."""
    key = "m22-bucket-independence-admin"
    try:
        for _ in range(RATE_LIMIT_MAX_FAILURES):
            admin_rate_limiter.record_failure(key)
        assert admin_rate_limiter.is_rate_limited(key) is True
        # Same key, other singleton → independent buckets, not rate-limited.
        assert auth_rate_limiter.is_rate_limited(key) is False
        assert auth_rate_limiter.get_backoff_seconds(key) == 0
    finally:
        admin_rate_limiter.reset(key)
        auth_rate_limiter.reset(key)


def test_auth_rate_limiter_buckets_independent_from_admin():
    """Exhausting the auth bucket leaves the admin bucket untouched (same key)."""
    key = "m22-bucket-independence-auth"
    try:
        for _ in range(RATE_LIMIT_MAX_FAILURES):
            auth_rate_limiter.record_failure(key)
        assert auth_rate_limiter.is_rate_limited(key) is True
        assert admin_rate_limiter.is_rate_limited(key) is False
        assert admin_rate_limiter.get_backoff_seconds(key) == 0
    finally:
        auth_rate_limiter.reset(key)
        admin_rate_limiter.reset(key)


def test_admin_rate_limiter_uses_shared_env_knobs():
    """admin_rate_limiter is built from the same env knobs as auth_rate_limiter."""
    assert admin_rate_limiter._max_failures == RATE_LIMIT_MAX_FAILURES
    assert admin_rate_limiter._window == RATE_LIMIT_WINDOW_SECONDS
    assert admin_rate_limiter._max_failures == auth_rate_limiter._max_failures
    assert admin_rate_limiter._window == auth_rate_limiter._window
