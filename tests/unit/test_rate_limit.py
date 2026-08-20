"""Unit tests for caldav_mcp.rate_limit — sliding-window rate limiter."""

import time

from caldav_mcp.rate_limit import RateLimiter


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
