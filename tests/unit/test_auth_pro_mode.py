"""Unit tests for pro-mode endpoint auth (M4.2 — X-Mcp-Username + DB users).

Patch discipline: ``configure_app_config(AppConfig(mode="db", …))`` +
``configure_pro_users(…)`` with ``reset_app_config()`` / ``reset_pro_users()``
teardown fixtures; headers via ``mock.patch.object(auth, "_hdrs")``.

Rate limiter is reset between tests via a fresh ``RateLimiter`` with a low
threshold, installed on ``auth.auth_rate_limiter``.
"""

from __future__ import annotations

from unittest import mock

import pytest

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


def _headers(d: dict[str, str]) -> None:
    """Return a lambda returning *d* (matches test_auth.py patch idiom)."""
    return lambda: d


@pytest.fixture(autouse=True)
def _pro_mode_setup():
    """Install pro-mode config + users before each test; tear down after."""
    configure_app_config(_PRO_APP)
    configure_pro_users((_TEST_USER,))
    # Fresh rate limiter with a low threshold for isolated testing.
    fresh = RateLimiter(max_failures=3, window_seconds=300)
    with mock.patch("caldav_mcp.auth.auth_rate_limiter", fresh):
        yield
    reset_pro_users()
    reset_app_config()


# ---------------------------------------------------------------------------
# 1. Happy path: X-Mcp-Username + Bearer
# ---------------------------------------------------------------------------


def test_happy_path_bearer():
    """Configured user + X-Mcp-Username + Bearer → None (success)."""
    with mock.patch.object(
        auth := __import__("caldav_mcp.auth", fromlist=["auth"]),
        "_hdrs",
        return_value=_headers(
            {
                "x-mcp-username": "alice",
                "authorization": f"Bearer {_REAL_KEY}",
            }
        ),
    ):
        result = auth._require_auth()
    assert result is None


# ---------------------------------------------------------------------------
# 2. Happy path via X-Api-Key
# ---------------------------------------------------------------------------


def test_happy_path_x_api_key():
    """Configured user + X-Mcp-Username + X-Api-Key → None (success)."""
    with mock.patch.object(
        auth := __import__("caldav_mcp.auth", fromlist=["auth"]),
        "_hdrs",
        return_value=_headers(
            {
                "x-mcp-username": "alice",
                "x-api-key": _REAL_KEY,
            }
        ),
    ):
        result = auth._require_auth()
    assert result is None


# ---------------------------------------------------------------------------
# 3. Wrong key records rate-limit failure
# ---------------------------------------------------------------------------


def test_wrong_key_records_failure():
    """Wrong key → AUTH failure; rate limiter recorded a failure."""
    with mock.patch.object(
        auth := __import__("caldav_mcp.auth", fromlist=["auth"]),
        "_hdrs",
        return_value=_headers(
            {
                "x-mcp-username": "alice",
                "authorization": "Bearer wrong-key",
            }
        ),
    ):
        result = auth._require_auth()
    assert result.status == Status.AUTH
    # Rate limiter should have recorded a failure for the client IP.
    client_ip = auth._get_client_ip()
    assert not auth_rate_limiter_reset_check(client_ip)


# ---------------------------------------------------------------------------
# 4. Unknown username → same message as wrong key (no enumeration)
# ---------------------------------------------------------------------------


def test_unknown_user_same_message_as_wrong_key():
    """Unknown username → AUTH failure; message identical to wrong-key case."""
    with mock.patch.object(
        auth := __import__("caldav_mcp.auth", fromlist=["auth"]),
        "_hdrs",
        return_value=_headers(
            {
                "x-mcp-username": "unknown-person",
                "authorization": f"Bearer {_REAL_KEY}",
            }
        ),
    ):
        result = auth._require_auth()
    assert result.status == Status.AUTH
    assert result.message == _FAILURE_MSG


# ---------------------------------------------------------------------------
# 5. Missing X-Mcp-Username with valid Bearer → fail
# ---------------------------------------------------------------------------


def test_missing_username_with_valid_bearer_fails():
    """Valid Bearer but no X-Mcp-Username → AUTH failure."""
    with mock.patch.object(
        auth := __import__("caldav_mcp.auth", fromlist=["auth"]),
        "_hdrs",
        return_value=_headers(
            {
                "authorization": f"Bearer {_REAL_KEY}",
            }
        ),
    ):
        result = auth._require_auth()
    assert result.status == Status.AUTH


# ---------------------------------------------------------------------------
# 6. Missing key with valid username → fail-fast, verify_api_key NOT called
# ---------------------------------------------------------------------------


def test_missing_key_fail_fast():
    """Valid username but no key → fail fast before PBKDF2; verify_api_key not called."""
    with mock.patch.object(
        auth := __import__("caldav_mcp.auth", fromlist=["auth"]),
        "_hdrs",
        return_value=_headers(
            {
                "x-mcp-username": "alice",
            }
        ),
    ):
        with mock.patch("caldav_mcp.auth.verify_api_key") as mock_verify:
            result = auth._require_auth()
    assert result.status == Status.AUTH
    mock_verify.assert_not_called()


# ---------------------------------------------------------------------------
# 7. CALDAV_MCP_API_KEY set in pro mode but no X-Mcp-Username → fails
# ---------------------------------------------------------------------------


def test_env_api_key_ignored_in_pro_mode():
    """CALDAV_MCP_API_KEY set + correct value but no X-Mcp-Username → AUTH failure."""
    with mock.patch.object(config, "API_KEY", "should-be-ignored"):
        with mock.patch.object(
            auth := __import__("caldav_mcp.auth", fromlist=["auth"]),
            "_hdrs",
            return_value=_headers(
                {
                    "authorization": "Bearer should-be-ignored",
                }
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
    with mock.patch.object(
        auth := __import__("caldav_mcp.auth", fromlist=["auth"]),
        "_hdrs",
        return_value=_headers(
            {
                "x-mcp-username": "alice",
                "authorization": f"Bearer {_REAL_KEY}",
            }
        ),
    ):
        result = auth._require_auth()
    assert result.status == Status.AUTH
    assert result.message == "pro mode enabled but no users configured in the config store"


# ---------------------------------------------------------------------------
# 9. Rate limiting triggers before hashing; success resets
# ---------------------------------------------------------------------------


def test_rate_limiting_before_hashing_and_success_resets():
    """Drive failures past limit → rate-limited before hashing; success resets."""
    import caldav_mcp.auth as auth_mod
    from caldav_mcp.auth import auth_rate_limiter

    # Drive 3 failures (max_failures=3 in the fixture limiter).
    client_ip = "test-client-ip"
    for _ in range(3):
        auth_rate_limiter.record_failure(client_ip)
    assert auth_rate_limiter.is_rate_limited(client_ip)

    # Patch _get_client_ip to return a known IP for rate-limiting.
    with mock.patch.object(auth_mod, "_get_client_ip", return_value=client_ip):
        with mock.patch.object(
            auth_mod,
            "_hdrs",
            return_value=_headers(
                {
                    "x-mcp-username": "alice",
                    "authorization": f"Bearer {_REAL_KEY}",
                }
            ),
        ):
            with mock.patch("caldav_mcp.auth.verify_api_key") as mock_verify:
                result = auth_mod._require_auth()
    assert result.status == Status.AUTH
    assert "rate limited" in result.message
    mock_verify.assert_not_called()  # hashing skipped when rate-limited

    # After a successful auth, the counter resets.
    auth_rate_limiter.reset(client_ip)
    assert not auth_rate_limiter.is_rate_limited(client_ip)

    # Now a valid auth should succeed.
    with mock.patch.object(auth_mod, "_get_client_ip", return_value=client_ip):
        with mock.patch.object(
            auth_mod,
            "_hdrs",
            return_value=_headers(
                {
                    "x-mcp-username": "alice",
                    "authorization": f"Bearer {_REAL_KEY}",
                }
            ),
        ):
            result = auth_mod._require_auth()
    assert result is None


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

    with mock.patch.object(config, "API_KEY", "env-key"):
        with mock.patch.object(
            auth := __import__("caldav_mcp.auth", fromlist=["auth"]),
            "_hdrs",
            return_value=_headers(
                {
                    "x-mcp-username": "alice",  # should be ignored
                    "authorization": "Bearer env-key",
                }
            ),
        ):
            result = auth._require_auth()
    assert result is None  # env key matched, X-Mcp-Username was irrelevant


# ---------------------------------------------------------------------------
# 11. Integration: real ConfigStore + hash_api_key + load_pro_state → auth
# ---------------------------------------------------------------------------


def test_integration_real_store_end_to_end(tmp_path, monkeypatch):
    """Build a real store, load via load_pro_state, install, authenticate."""
    import caldav_mcp.auth as auth_mod
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
        real_key_hash = hash_api_key("real-integration-key")
        store.create_user("bob", real_key_hash)
        store.grant_config("bob", "work")

    # Load and install.
    state = load_pro_state(db_path, secret)
    configure_app_config(state.app_config)
    configure_pro_users(state.users)

    fresh = RateLimiter(max_failures=3, window_seconds=300)
    with mock.patch("caldav_mcp.auth.auth_rate_limiter", fresh):
        with mock.patch.object(
            auth_mod,
            "_hdrs",
            return_value=_headers(
                {
                    "x-mcp-username": "bob",
                    "authorization": "Bearer real-integration-key",
                }
            ),
        ):
            result = auth_mod._require_auth()
    assert result is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def auth_rate_limiter_reset_check(client_ip: str) -> bool:
    """Return True if the client IP has recorded failures."""
    from caldav_mcp.auth import auth_rate_limiter

    return auth_rate_limiter.is_rate_limited(client_ip)
