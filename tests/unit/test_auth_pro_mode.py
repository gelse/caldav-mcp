"""Unit tests for pro-mode endpoint auth (M4.2 — X-Mcp-Username + DB users).

Patch discipline: ``configure_app_config(AppConfig(mode="db", …))`` +
``configure_pro_users(…)`` with ``reset_app_config()`` / ``reset_pro_users()``
teardown fixtures; headers via ``mock.patch.object(auth, "_hdrs")``.

The rate limiter is isolated per test by installing a fresh ``RateLimiter``
with a low threshold on ``auth.auth_rate_limiter``; the fixture yields that
limiter so tests can assert on genuinely recorded failures.
"""

from __future__ import annotations

from unittest import mock

import pytest

import caldav_mcp.auth as auth
import caldav_mcp.config as config
from caldav_mcp.app_config import (
    AppConfig,
    Calendar,
    Config,
    Remote,
    configure_app_config,
    reset_app_config,
)
from caldav_mcp.auth import (
    _FAILURE_MSG,
    configure_pro_users,
    reset_pro_users,
)
from caldav_mcp.config_crypto import encrypt_secret
from caldav_mcp.config_store import RemoteRecord
from caldav_mcp.db_loader import ProUser
from caldav_mcp.errors import Status
from caldav_mcp.key_hash import hash_api_key
from caldav_mcp.rate_limit import RateLimiter

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_FAKE_CONFIG = Config(
    name="default",
    remotes=(
        Remote(
            name="nc",
            url="https://cal.example/dav",
            auth_mode="direct",
            username="alice",
            password="pass",
        ),
    ),
    calendars=(("nc", (Calendar(name="work"),)),),
)

_REAL_KEY = "my-secret-key-123"
_USER_HASH = hash_api_key(_REAL_KEY)
_TEST_USER = ProUser(username="alice", key_hash=_USER_HASH, config_names=("default",))
_PRO_APP = AppConfig(mode="db", config=None, configs=(_FAKE_CONFIG,))

_VALID_BEARER = {"x-mcp-username": "alice", "authorization": f"Bearer {_REAL_KEY}"}


def _headers(d: dict[str, str]):
    """Return an ``auth._hdrs``-compatible zero-arg callable returning *d*."""
    return lambda: d


@pytest.fixture(autouse=True)
def _pro_mode_setup():
    """Install pro-mode config + users and a fresh rate limiter per test."""
    configure_app_config(_PRO_APP)
    configure_pro_users((_TEST_USER,))
    fresh = RateLimiter(max_failures=3, window_seconds=300)
    with mock.patch("caldav_mcp.auth.auth_rate_limiter", fresh):
        yield fresh
    reset_pro_users()
    reset_app_config()


# ---------------------------------------------------------------------------
# 1. Happy path: X-Mcp-Username + Bearer
# ---------------------------------------------------------------------------


def test_happy_path_bearer():
    """Configured user + X-Mcp-Username + Bearer → None; audit method="db-user"."""
    with (
        mock.patch.object(auth, "_hdrs", return_value=_headers(dict(_VALID_BEARER))),
        mock.patch("caldav_mcp.auth.log_auth_attempt") as mock_audit,
    ):
        result = auth._require_auth()
    assert result is None
    mock_audit.assert_called_once()
    assert mock_audit.call_args.kwargs.get("method") == "db-user"
    assert mock_audit.call_args.kwargs.get("success") is True


# ---------------------------------------------------------------------------
# 2. Happy path via X-Api-Key
# ---------------------------------------------------------------------------


def test_happy_path_x_api_key():
    """Configured user + X-Mcp-Username + X-Api-Key → None (success)."""
    with mock.patch.object(
        auth,
        "_hdrs",
        return_value=_headers({"x-mcp-username": "alice", "x-api-key": _REAL_KEY}),
    ):
        result = auth._require_auth()
    assert result is None


# ---------------------------------------------------------------------------
# 3. Wrong key records rate-limit failure
# ---------------------------------------------------------------------------


def test_wrong_key_records_failure(_pro_mode_setup):
    """Wrong key → AUTH failure; the limiter must have recorded the attempt."""
    limiter = _pro_mode_setup
    client_ip = "203.0.113.7"
    with (
        mock.patch.object(auth, "_get_client_ip", return_value=client_ip),
        mock.patch.object(
            auth,
            "_hdrs",
            return_value=_headers({"x-mcp-username": "alice", "authorization": "Bearer wrong-key"}),
        ),
    ):
        result = auth._require_auth()
    assert result.status == Status.AUTH
    # Top up to the threshold.  If the failed attempt had NOT been recorded the
    # count would stay below the limit and this assertion would fail.
    limiter.record_failure(client_ip)
    limiter.record_failure(client_ip)
    assert limiter.is_rate_limited(client_ip)


# ---------------------------------------------------------------------------
# 4. Unknown username → same message as wrong key (no enumeration)
# ---------------------------------------------------------------------------


def test_unknown_user_same_message_as_wrong_key():
    """Unknown username and wrong key yield byte-identical AUTH messages."""
    with mock.patch.object(
        auth,
        "_hdrs",
        return_value=_headers({"x-mcp-username": "alice", "authorization": "Bearer wrong-key"}),
    ):
        wrong_key = auth._require_auth()
    with mock.patch.object(
        auth,
        "_hdrs",
        return_value=_headers({"x-mcp-username": "ghost", "authorization": f"Bearer {_REAL_KEY}"}),
    ):
        unknown_user = auth._require_auth()
    assert wrong_key.status == Status.AUTH
    assert unknown_user.status == Status.AUTH
    assert wrong_key.message == unknown_user.message == _FAILURE_MSG


# ---------------------------------------------------------------------------
# 5. Missing X-Mcp-Username with valid Bearer → fail
# ---------------------------------------------------------------------------


def test_missing_username_with_valid_bearer_fails():
    """Valid Bearer but no X-Mcp-Username → AUTH failure."""
    with mock.patch.object(
        auth,
        "_hdrs",
        return_value=_headers({"authorization": f"Bearer {_REAL_KEY}"}),
    ):
        result = auth._require_auth()
    assert result.status == Status.AUTH


# ---------------------------------------------------------------------------
# 6. Missing key with valid username → fail-fast, verify_api_key NOT called
# ---------------------------------------------------------------------------


def test_missing_key_fail_fast():
    """Valid username but no key → fail fast before PBKDF2; verify not called."""
    with (
        mock.patch.object(auth, "_hdrs", return_value=_headers({"x-mcp-username": "alice"})),
        mock.patch("caldav_mcp.auth.verify_api_key") as mock_verify,
    ):
        result = auth._require_auth()
    assert result.status == Status.AUTH
    mock_verify.assert_not_called()


# ---------------------------------------------------------------------------
# 7. CALDAV_MCP_API_KEY set in pro mode but no X-Mcp-Username → fails
# ---------------------------------------------------------------------------


def test_env_api_key_ignored_in_pro_mode():
    """CALDAV_MCP_API_KEY set + correct value but no X-Mcp-Username → AUTH failure."""
    with (
        mock.patch.object(config, "API_KEY", "should-be-ignored"),
        mock.patch.object(
            auth,
            "_hdrs",
            return_value=_headers({"authorization": "Bearer should-be-ignored"}),
        ),
    ):
        result = auth._require_auth()
    assert result.status == Status.AUTH


# ---------------------------------------------------------------------------
# 8. No users configured → dedicated message
# ---------------------------------------------------------------------------


def test_no_users_configured():
    """No users installed → AUTH failure with dedicated message."""
    reset_pro_users()
    with mock.patch.object(auth, "_hdrs", return_value=_headers(dict(_VALID_BEARER))):
        result = auth._require_auth()
    assert result.status == Status.AUTH
    assert result.message == "pro mode enabled but no users configured in the config store"


# ---------------------------------------------------------------------------
# 9. Rate limiting triggers before hashing; success resets
# ---------------------------------------------------------------------------


def test_rate_limiting_before_hashing_and_success_resets(_pro_mode_setup):
    """Failures at the limit are rejected before hashing; success resets."""
    limiter = _pro_mode_setup
    client_ip = "198.51.100.9"

    for _ in range(3):
        limiter.record_failure(client_ip)
    assert limiter.is_rate_limited(client_ip)

    with (
        mock.patch.object(auth, "_get_client_ip", return_value=client_ip),
        mock.patch.object(auth, "_hdrs", return_value=_headers(dict(_VALID_BEARER))),
        mock.patch("caldav_mcp.auth.verify_api_key") as mock_verify,
    ):
        limited = auth._require_auth()
    assert limited.status == Status.AUTH
    assert "rate limited" in limited.message
    mock_verify.assert_not_called()  # hashing skipped while rate-limited

    # Success must reset the counter: with two failures already recorded a
    # single later failure stays below the threshold only if the success reset
    # the history.
    limiter.reset(client_ip)
    limiter.record_failure(client_ip)
    limiter.record_failure(client_ip)
    with (
        mock.patch.object(auth, "_get_client_ip", return_value=client_ip),
        mock.patch.object(auth, "_hdrs", return_value=_headers(dict(_VALID_BEARER))),
    ):
        ok = auth._require_auth()
    assert ok is None
    limiter.record_failure(client_ip)
    assert not limiter.is_rate_limited(client_ip)


# ---------------------------------------------------------------------------
# 10. Simple-mode non-interference: X-Mcp-Username ignored in env mode
# ---------------------------------------------------------------------------


def test_simple_mode_x_mcp_username_ignored():
    """With AppConfig(mode="env", …) + API_KEY set, X-Mcp-Username is ignored."""
    env_app = AppConfig(
        mode="env",
        config=Config(
            name="default",
            remotes=(
                Remote(
                    name="default",
                    url="https://cal.example/dav",
                    auth_mode="direct",
                    username="u",
                    password="p",
                ),
            ),
            calendars=(),
        ),
    )
    configure_app_config(env_app)
    reset_pro_users()  # no pro users

    with (
        mock.patch.object(config, "API_KEY", "env-key"),
        mock.patch.object(
            auth,
            "_hdrs",
            return_value=_headers(
                {
                    "x-mcp-username": "alice",  # should be ignored
                    "authorization": "Bearer env-key",
                }
            ),
        ),
    ):
        result = auth._require_auth()
    assert result is None  # env key matched, X-Mcp-Username was irrelevant


# ---------------------------------------------------------------------------
# 11. Integration: real ConfigStore + hash_api_key + load_pro_state → auth
# ---------------------------------------------------------------------------


def test_integration_real_store_end_to_end(tmp_path, monkeypatch):
    """Build a real store, load via load_pro_state, install, authenticate."""
    from caldav_mcp.config_store import ConfigStore
    from caldav_mcp.db_loader import load_pro_state

    db_path = str(tmp_path / "test.db")
    secret = "test-master-secret"

    # Set CALDAV_MCP_CONFIG_SECRET so encrypt_secret can derive the key.
    monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", secret)

    # Build the store via the real ConfigStore API.
    with ConfigStore(db_path) as store:
        store.create_config("work")
        store.create_remote(
            "work",
            RemoteRecord(
                config_name="work",
                name="nc",
                url="https://cal.example/dav",
                auth_mode="direct",
                username="bob",
                password_enc=encrypt_secret("cal-password-123"),
            ),
        )
        store.create_calendar("work", "nc", "personal")
        store.create_user("bob", hash_api_key("real-integration-key"))
        store.grant_config("bob", "work")

    # Load and install.
    state = load_pro_state(db_path, secret)
    configure_app_config(state.app_config)
    configure_pro_users(state.users)

    fresh = RateLimiter(max_failures=3, window_seconds=300)
    with (
        mock.patch("caldav_mcp.auth.auth_rate_limiter", fresh),
        mock.patch.object(
            auth,
            "_hdrs",
            return_value=_headers(
                {
                    "x-mcp-username": "bob",
                    "authorization": "Bearer real-integration-key",
                }
            ),
        ),
    ):
        result = auth._require_auth()
    assert result is None
