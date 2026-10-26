"""Unit tests for the admin REST API (M2.3 — guard + GET routes).

**Test vehicle (pinned by the milestone):** Starlette ``TestClient`` against
the FastMCP HTTP app (``mcp.http_app()``) — the guard consumes a real
``Request`` and route-level 404/405 semantics are part of acceptance.  The
pinned vehicle proved compatible with the installed fastmcp 3.4.7, so no
fallback to direct-handler invocation was needed.

Fixture pattern mirrors ``tests/unit/test_auth_pro_mode.py``: autouse
``configure_app_config``/``configure_pro_users`` + env patching + teardown;
the admin rate limiter is isolated per test by installing a fresh
``RateLimiter`` on ``caldav_mcp.admin.admin_rate_limiter`` (yielded so tests
can assert on genuinely recorded failures).

All tests are Docker-free: the store is a per-test SQLite file in ``tmp_path``.
"""

from __future__ import annotations

import sqlite3
from unittest import mock

import pytest
from fastmcp import FastMCP
from starlette.testclient import TestClient

import caldav_mcp.admin as admin
import caldav_mcp.config as config
from caldav_mcp import mcp
from caldav_mcp.admin import configure_admin_routes
from caldav_mcp.app_config import (
    AppConfig,
    Calendar,
    Config,
    Remote,
    configure_app_config,
    reset_app_config,
)
from caldav_mcp.config_store import ConfigStore
from caldav_mcp.key_hash import hash_api_key
from caldav_mcp.rate_limit import RateLimiter

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

_ADMIN_KEY = "unit-test-admin-key"
_BEARER = {"Authorization": f"Bearer {_ADMIN_KEY}"}

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
_PRO_APP = AppConfig(mode="db", config=None, configs=(_FAKE_CONFIG,))


def _error_of(response) -> str:
    """Return the ``error`` message of a ``{"error": ...}`` JSON body."""
    body = response.json()
    assert isinstance(body, dict) and isinstance(body.get("error"), str), body
    return body["error"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _register_admin_routes() -> None:
    """Register the M2 admin routes on the shared instance once per session."""
    configure_admin_routes(mcp)


@pytest.fixture(autouse=True)
def _admin_setup(tmp_path, monkeypatch):
    """Install pro-mode config, a seeded store, env, key and a fresh limiter.

    Yields the fresh ``RateLimiter`` installed on ``caldav_mcp.admin`` so
    tests can assert on genuinely recorded failures (fixture pattern of
    ``test_auth_pro_mode.py``).
    """
    db_path = str(tmp_path / "admin-store.db")
    with ConfigStore(db_path) as store:
        store.create_config("default")
        store.create_user("alice", hash_api_key("alice-unit-key"))
        store.grant_config("alice", "default")
        store.create_user("bob", hash_api_key("bob-unit-key"))
    monkeypatch.setenv("CALDAV_MCP_DB_PATH", db_path)
    monkeypatch.setattr(config, "ADMIN_KEY", _ADMIN_KEY, raising=True)
    configure_app_config(_PRO_APP)
    fresh = RateLimiter(max_failures=3, window_seconds=300)
    with mock.patch.object(admin, "admin_rate_limiter", fresh):
        yield fresh
    reset_app_config()


@pytest.fixture(scope="module")
def client():
    """Module-scoped TestClient over the FastMCP HTTP app (routes registered)."""
    with TestClient(mcp.http_app()) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# 1. Happy paths: 200 with admin key
# ---------------------------------------------------------------------------


def test_list_users_200_with_bearer(client):
    """GET /admin/users with Bearer key → 200, users in insertion order."""
    with mock.patch.object(admin, "log_admin_operation") as mock_admin_audit:
        resp = client.get("/admin/users", headers=_BEARER)
    assert resp.status_code == 200
    users = resp.json()["users"]
    assert [u["username"] for u in users] == ["alice", "bob"]
    # User objects: exactly {username, configs} — never key material.
    for user in users:
        assert set(user) == {"username", "configs"}
        assert isinstance(user["configs"], list)
    alice = users[0]
    assert alice["configs"] == ["default"]
    assert "key_hash" not in resp.text and '"key"' not in resp.text
    # Completed CRUD outcome is audited.
    mock_admin_audit.assert_called_once_with(
        action="list_users", status="ok", client_ip="testclient"
    )


def test_list_users_200_with_x_api_key(client):
    """GET /admin/users via X-Api-Key transport → 200."""
    resp = client.get("/admin/users", headers={"X-Api-Key": _ADMIN_KEY})
    assert resp.status_code == 200
    assert len(resp.json()["users"]) == 2


def test_get_user_200(client):
    """GET /admin/users/alice → 200 with the {username, configs} object."""
    with mock.patch.object(admin, "log_admin_operation") as mock_admin_audit:
        resp = client.get("/admin/users/alice", headers=_BEARER)
    assert resp.status_code == 200
    assert resp.json() == {"username": "alice", "configs": ["default"]}
    assert "key_hash" not in resp.text and '"key"' not in resp.text
    mock_admin_audit.assert_called_once_with(
        action="get_user", status="ok", target_user="alice", client_ip="testclient"
    )


def test_get_user_404_unknown_user(client):
    """GET for a user missing from the store → 404 with an error body."""
    with mock.patch.object(admin, "log_admin_operation") as mock_admin_audit:
        resp = client.get("/admin/users/ghost", headers=_BEARER)
    assert resp.status_code == 404
    assert "ghost" in _error_of(resp)
    mock_admin_audit.assert_called_once_with(
        action="get_user", status="error", target_user="ghost", client_ip="testclient"
    )


# ---------------------------------------------------------------------------
# 2. Missing / invalid admin key → 401 + audit + record_failure
# ---------------------------------------------------------------------------


def test_missing_key_401_records_failure(client, _admin_setup):
    """No key → 401; the failure is recorded in the admin limiter bucket."""
    limiter = _admin_setup
    resp = client.get("/admin/users")
    assert resp.status_code == 401
    assert "error" in resp.json()
    # Top up to the threshold: only passes if the 401 recorded one failure.
    limiter.record_failure("testclient")
    limiter.record_failure("testclient")
    assert limiter.is_rate_limited("testclient") is True


def test_wrong_key_401_audits_and_records_failure(client, _admin_setup):
    """Wrong key → 401 + log_auth_attempt(method='admin-api') + record_failure."""
    limiter = _admin_setup
    with (
        mock.patch.object(admin, "log_auth_attempt") as mock_auth_audit,
        mock.patch.object(admin, "_const_eq", wraps=admin._const_eq) as mock_const_eq,
    ):
        resp = client.get("/admin/users", headers={"Authorization": "Bearer wrong-key"})
    assert resp.status_code == 401
    _error_of(resp)
    mock_auth_audit.assert_called_once()
    kwargs = mock_auth_audit.call_args.kwargs
    assert kwargs["success"] is False
    assert kwargs["method"] == "admin-api"
    assert kwargs["reason"] == "invalid key"
    assert kwargs["client_ip"] == "testclient"
    # Constant-time comparison really ran against the configured key.
    mock_const_eq.assert_called_once_with("wrong-key", _ADMIN_KEY)
    # Top up to the threshold: only passes if the 401 recorded one failure.
    limiter.record_failure("testclient")
    limiter.record_failure("testclient")
    assert limiter.is_rate_limited("testclient") is True


def test_success_resets_limiter_and_audits(client, _admin_setup):
    """Success → log_auth_attempt(success=True, method='admin-api') + reset."""
    limiter = _admin_setup
    limiter.record_failure("testclient")
    limiter.record_failure("testclient")
    with mock.patch.object(admin, "log_auth_attempt") as mock_auth_audit:
        resp = client.get("/admin/users", headers=_BEARER)
    assert resp.status_code == 200
    kwargs = mock_auth_audit.call_args.kwargs
    assert kwargs["success"] is True
    assert kwargs["method"] == "admin-api"
    # Without the success reset the two recorded failures would persist; two
    # more would then reach the threshold of 3 and the request below would 429.
    limiter.record_failure("testclient")
    limiter.record_failure("testclient")
    assert limiter.is_rate_limited("testclient") is False
    assert client.get("/admin/users", headers=_BEARER).status_code == 200


# ---------------------------------------------------------------------------
# 3. Rate limiting → 429 + Retry-After (per-IP, X-Forwarded-For first hop)
# ---------------------------------------------------------------------------


def test_rate_limited_429_with_retry_after(client, _admin_setup):
    """An IP at the failure threshold → 429 with a Retry-After header."""
    limiter = _admin_setup
    for _ in range(3):
        limiter.record_failure("testclient")
    with mock.patch.object(admin, "log_auth_attempt") as mock_auth_audit:
        resp = client.get("/admin/users", headers=_BEARER)
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) >= 1
    assert "rate limited" in _error_of(resp)
    # The limited request performs no key comparison or auth audit.
    mock_auth_audit.assert_not_called()


def test_forwarded_for_first_hop_is_the_bucket_key(client, _admin_setup):
    """Failures key on the X-Forwarded-For first hop, per forwarded IP."""
    limiter = _admin_setup
    fwd_a = {"X-Forwarded-For": "203.0.113.9, 10.0.0.1", **_BEARER}
    bad_a = {"X-Forwarded-For": "203.0.113.9, 10.0.0.1", "Authorization": "Bearer wrong"}
    for _ in range(3):
        assert client.get("/admin/users", headers=bad_a).status_code == 401
    # Same forwarded IP is now limited …
    assert client.get("/admin/users", headers=fwd_a).status_code == 429
    # … while a different forwarded IP and the direct peer stay untouched.
    fwd_b = {"X-Forwarded-For": "203.0.113.10", **_BEARER}
    assert client.get("/admin/users", headers=fwd_b).status_code == 200
    assert client.get("/admin/users", headers=_BEARER).status_code == 200
    assert limiter.is_rate_limited("testclient") is False


def test_whitespace_forwarded_for_falls_back_to_peer(client, _admin_setup):
    """Whitespace-only X-Forwarded-For must not collapse callers into one bucket."""
    limiter = _admin_setup
    bad = {"X-Forwarded-For": "   ", "Authorization": "Bearer wrong"}
    for _ in range(3):
        assert client.get("/admin/users", headers=bad).status_code == 401
    # Failures landed on the socket-peer bucket, not a shared "" bucket.
    assert limiter.is_rate_limited("testclient") is True


# ---------------------------------------------------------------------------
# 4. Fail-closed / mode gating → 404
# ---------------------------------------------------------------------------


def test_admin_key_unset_404_fail_closed_before_comparison(client, _admin_setup, monkeypatch):
    """ADMIN_KEY empty → 404 before any comparison (const-eq never runs)."""
    limiter = _admin_setup
    monkeypatch.setattr(config, "ADMIN_KEY", "")
    limiter.record_failure("testclient")
    limiter.record_failure("testclient")
    with (
        mock.patch.object(admin, "log_auth_attempt") as mock_auth_audit,
        mock.patch.object(admin, "_const_eq") as mock_const_eq,
    ):
        resp = client.get("/admin/users", headers=_BEARER)
    assert resp.status_code == 404
    mock_const_eq.assert_not_called()
    mock_auth_audit.assert_not_called()
    # No failure recorded: two pre-recorded failures stay below the threshold
    # of 3 — a third (erroneously recorded by the 404 path) would trip it.
    assert limiter.is_rate_limited("testclient") is False


def test_simple_mode_404_guard_double_check(client):
    """With the singleton in env (simple) mode the guard itself 404s."""
    env_app = AppConfig(mode="env", config=_FAKE_CONFIG)
    configure_app_config(env_app)
    resp = client.get("/admin/users", headers=_BEARER)
    assert resp.status_code == 404
    assert _error_of(resp) == "not found"


def test_framework_404_when_routes_never_registered():
    """Without configure_admin_routes the framework serves 404 for /admin/*."""
    bare = FastMCP("no-admin-routes")
    with TestClient(bare.http_app()) as bare_client:
        assert bare_client.get("/admin/users").status_code == 404
        assert bare_client.get("/admin/users/alice").status_code == 404


# ---------------------------------------------------------------------------
# 5. Write routes unregistered: 405 where the path exists, 404 otherwise
# ---------------------------------------------------------------------------


def test_write_on_registered_paths_405(client):
    """POST /admin/users and DELETE /admin/users/{u} → 405 (GET only)."""
    resp = client.post("/admin/users", json={"username": "x", "key": "y"})
    assert resp.status_code == 405
    assert "GET" in resp.headers["allow"]

    resp = client.delete("/admin/users/alice")
    assert resp.status_code == 405
    assert "GET" in resp.headers["allow"]


def test_future_write_paths_404(client):
    """Paths that gain routes only in M3/M4 → framework 404, not 405."""
    cases = (
        ("put", "/admin/users/alice/key"),
        ("post", "/admin/users/alice/key"),
        ("post", "/admin/users/alice/configs"),
        ("put", "/admin/users/alice/configs"),
        ("delete", "/admin/users/alice/configs/default"),
    )
    for method, path in cases:
        resp = getattr(client, method)(path)
        assert resp.status_code == 404, (method, path, resp.status_code)


# ---------------------------------------------------------------------------
# 6. Username transport guard → 400 (after auth)
# ---------------------------------------------------------------------------


def test_username_too_long_400(client):
    """Path username longer than 128 chars → 400 transport-guard error."""
    resp = client.get("/admin/users/" + "u" * 129, headers=_BEARER)
    assert resp.status_code == 400
    assert "128" in _error_of(resp)


def test_username_control_chars_400(client):
    """Path username containing NUL/control chars → 400 transport guard."""
    resp = client.get("/admin/users/a%00b", headers=_BEARER)
    assert resp.status_code == 400
    assert "control" in _error_of(resp)


def test_transport_guard_runs_after_auth(client):
    """Unauthenticated bad username → 401, not 400 (no validation leak)."""
    resp = client.get("/admin/users/" + "u" * 129)
    assert resp.status_code == 401


def test_username_transport_helper_slash():
    """The '/' rejection is reachable via the helper (routing already 404s)."""
    assert admin._username_transport_error("a/b") is not None
    assert admin._username_transport_error("plain-user") is None


# ---------------------------------------------------------------------------
# 7. Store error mapping (single choke point, Design (d))
# ---------------------------------------------------------------------------


def test_store_unavailable_503_with_retry_hint(client, tmp_path, monkeypatch):
    """Unreadable store (path is a directory) → 503 with retry hint."""
    monkeypatch.setenv("CALDAV_MCP_DB_PATH", str(tmp_path))
    resp = client.get("/admin/users", headers=_BEARER)
    assert resp.status_code == 503
    assert "retry" in _error_of(resp).lower()


def test_store_schema_newer_503(client, tmp_path):
    """Store schema version newer than the code → 503."""
    conn = sqlite3.connect(str(tmp_path / "admin-store.db"))
    conn.execute("UPDATE schema_version SET version = 99")
    conn.commit()
    conn.close()
    resp = client.get("/admin/users", headers=_BEARER)
    assert resp.status_code == 503
    assert "error" in resp.json()


def test_unexpected_error_500_generic_message(client):
    """Non-store exception → 500 with generic body; detail logged only."""
    with (
        mock.patch.object(admin, "ConfigStore", side_effect=ValueError("boom-detail")),
        mock.patch.object(admin, "log") as mock_log,
        mock.patch.object(admin, "log_admin_operation") as mock_admin_audit,
    ):
        resp = client.get("/admin/users", headers=_BEARER)
    assert resp.status_code == 500
    body = resp.json()
    assert body == {"error": "internal server error"}
    assert "boom-detail" not in resp.text
    mock_log.error.assert_called_once()
    assert "boom-detail" in str(mock_log.error.call_args)
    # The failed store outcome is still audited as an admin operation error.
    mock_admin_audit.assert_called_once_with(
        action="list_users", status="error", client_ip="testclient"
    )
