"""Unit tests for the admin REST API (M2.3 guard + GET routes, M3.1 write routes).

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

import json
import logging
import os
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
from caldav_mcp.config_store import ConfigStore, RemoteRecord, StoreConflictError
from caldav_mcp.key_hash import hash_api_key, verify_api_key
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


def _add_passthrough_configs(db_path: str) -> None:
    """Add two configs, each carrying a passthrough remote (write cases).

    Granting both to one user would let them reach two passthrough remotes —
    the single-passthrough constraint the store enforces inside its own
    transactions (M3.1 write cases: grant → 400, replace → 400).
    """
    with ConfigStore(db_path) as store:
        for name in ("pt-one", "pt-two"):
            store.create_config(name)
            store.create_remote(
                config_name=name,
                remote=RemoteRecord(
                    config_name=name,
                    name="relay",
                    url="https://cal.example/dav",
                    auth_mode="passthrough",
                    username="",
                    password_enc="",
                ),
            )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _register_admin_routes() -> None:
    """Register the M2 read + M3 write admin routes on the shared instance once."""
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
# 5. Write routes present (M3.1): 401 unauthenticated / 405 wrong method /
#    404 only for the M4 rotation route (non-goal) and disabled API
# ---------------------------------------------------------------------------


def test_write_routes_exist_and_require_auth(client, _admin_setup):
    """Every M3 write route is registered and guarded: no key → 401."""
    limiter = _admin_setup
    cases = (
        ("post", "/admin/users", {"username": "x", "key": "y"}),
        ("delete", "/admin/users/alice", None),
        ("post", "/admin/users/alice/configs", {"config": "default"}),
        ("put", "/admin/users/alice/configs", {"configs": ["default"]}),
        ("delete", "/admin/users/alice/configs/default", None),
    )
    for method, path, body in cases:
        fn = getattr(client, method)
        resp = fn(path, json=body) if body is not None else fn(path)
        assert resp.status_code == 401, (method, path, resp.status_code)
        # Each 401 records a failure; clear it so the fifth request is not
        # rate-limited before reaching this route's guard.
        limiter.reset("testclient")


def test_wrong_method_on_routed_paths_405(client):
    """Methods outside table (d) on routed paths → 405, not 404/401."""
    resp = client.put("/admin/users", json={})
    assert resp.status_code == 405

    resp = client.put("/admin/users/alice", json={})
    assert resp.status_code == 405

    # /admin/users/{username}/configs exists with POST+PUT only.
    resp = client.delete("/admin/users/alice/configs")
    assert resp.status_code == 405


def test_rotation_route_absent_404(client):
    """``/admin/users/{u}/key`` (M4 rotation) stays unregistered → 404."""
    cases = (
        ("get", "/admin/users/alice/key"),
        ("post", "/admin/users/alice/key"),
        ("put", "/admin/users/alice/key"),
        ("delete", "/admin/users/alice/key"),
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


# ---------------------------------------------------------------------------
# 8. POST /admin/users (M3.1): 201 / 400 / 409 — hashed key, no secrets
# ---------------------------------------------------------------------------


def test_create_user_201_hashes_key_and_audits(client):
    """201 with the user payload + restart_required; key stored hashed only."""
    with mock.patch.object(admin, "log_admin_operation") as mock_admin_audit:
        resp = client.post(
            "/admin/users",
            json={"username": "carol", "key": "carol-plain-key"},
            headers=_BEARER,
        )
    assert resp.status_code == 201
    body = resp.json()
    # User payload + restart signal; never key material (Design (f)).
    assert set(body) == {"username", "configs", "restart_required"}
    assert body["username"] == "carol"
    assert body["configs"] == []
    assert body["restart_required"] is True
    assert "carol-plain-key" not in resp.text
    assert "key_hash" not in resp.text
    # Completed CRUD outcome audited; audit arguments carry no secrets.
    mock_admin_audit.assert_called_once_with(
        action="create_user", status="ok", target_user="carol", client_ip="testclient"
    )
    assert "carol-plain-key" not in repr(mock_admin_audit.call_args)
    # The store holds the PBKDF2 hash — the plaintext verifies, never equals.
    with ConfigStore(os.environ["CALDAV_MCP_DB_PATH"]) as store:
        record = store.get_user("carol")
    assert record.key_hash != "carol-plain-key"
    assert verify_api_key("carol-plain-key", record.key_hash)
    # The new user is immediately visible via the read API (restart only
    # gates MCP auth, not the admin read surface).
    resp = client.get("/admin/users/carol", headers=_BEARER)
    assert resp.status_code == 200
    assert resp.json() == {"username": "carol", "configs": []}


def test_create_user_emits_admin_audit_event_without_secrets(client, caplog):
    """Real audit line: ``event: admin`` with action/target and no key material."""
    with caplog.at_level(logging.INFO, logger="caldav-mcp.audit"):
        resp = client.post(
            "/admin/users",
            json={"username": "erin", "key": "erin-plain-key"},
            headers=_BEARER,
        )
    assert resp.status_code == 201
    audit_records = [r for r in caplog.records if r.name == "caldav-mcp.audit"]
    entries = [json.loads(r.getMessage()) for r in audit_records]
    admin_entries = [e for e in entries if e.get("event") == "admin"]
    assert admin_entries, entries
    entry = admin_entries[-1]
    assert entry["action"] == "create_user"
    assert entry["status"] == "ok"
    assert entry["target_user"] == "erin"
    assert entry["client_ip"] == "testclient"
    # SECURITY: no plaintext key or key hash in any audit line.
    blob = " ".join(r.getMessage() for r in audit_records)
    assert "erin-plain-key" not in blob
    assert "key_hash" not in blob


def test_create_user_409_duplicate(client):
    """Duplicate username → 409 (StoreConflictError from create_user)."""
    resp = client.post(
        "/admin/users",
        json={"username": "alice", "key": "whatever"},
        headers=_BEARER,
    )
    assert resp.status_code == 409
    assert "already exists" in _error_of(resp)


def test_create_user_400_missing_or_invalid_fields(client):
    """API-owned body validation → 400; nothing gets created."""
    cases = (
        ({}, "username"),  # missing username
        ({"username": 42, "key": "k"}, "username"),  # non-string username
        ({"username": "u"}, "key"),  # missing key
        ({"username": "u", "key": ""}, "key"),  # empty key (CLI-mirrored check)
        ({"username": "u", "key": 5}, "key"),  # non-string key
    )
    for payload, field in cases:
        resp = client.post("/admin/users", json=payload, headers=_BEARER)
        assert resp.status_code == 400, payload
        assert field in _error_of(resp), payload
    assert client.get("/admin/users/u", headers=_BEARER).status_code == 404


def test_create_user_400_empty_username_via_store_validation(client):
    """Empty username is the *store's* rule (Design (e)) → 400 via choke point."""
    resp = client.post("/admin/users", json={"username": "", "key": "k"}, headers=_BEARER)
    assert resp.status_code == 400
    assert "username" in _error_of(resp)
    assert "must not be empty" in _error_of(resp)


def test_create_user_400_malformed_json(client):
    """Malformed JSON body → 400 (edge-case table)."""
    resp = client.post(
        "/admin/users",
        content="{not-json",
        headers={**_BEARER, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert "json" in _error_of(resp).lower()


def test_create_user_400_wrong_content_type(client):
    """Wrong Content-Type → 400 even when the body parses (edge-case table)."""
    resp = client.post(
        "/admin/users",
        content='{"username": "u", "key": "k"}',
        headers={**_BEARER, "Content-Type": "text/plain"},
    )
    assert resp.status_code == 400
    assert "content-type" in _error_of(resp).lower()


def test_create_user_400_non_object_body(client):
    """A JSON array body → 400 (body shape is API-owned)."""
    resp = client.post("/admin/users", json=["not", "an", "object"], headers=_BEARER)
    assert resp.status_code == 400
    assert "object" in _error_of(resp)


def test_guard_runs_before_body_parse_on_writes(client):
    """Unauthenticated POST with a malformed body → 401, not 400."""
    resp = client.post(
        "/admin/users",
        content="{bad",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 9. DELETE /admin/users/{username} (M3.1): 200 + body / 404 / 400
# ---------------------------------------------------------------------------


def test_delete_user_200_with_restart_body(client):
    """Create → DELETE → 200 {deleted, username, restart_required} → 404 on GET."""
    created = client.post(
        "/admin/users",
        json={"username": "dave", "key": "dave-key"},
        headers=_BEARER,
    )
    assert created.status_code == 201
    with mock.patch.object(admin, "log_admin_operation") as mock_admin_audit:
        resp = client.delete("/admin/users/dave", headers=_BEARER)
    assert resp.status_code == 200
    assert resp.json() == {
        "deleted": True,
        "username": "dave",
        "restart_required": True,
    }
    mock_admin_audit.assert_called_once_with(
        action="delete_user", status="ok", target_user="dave", client_ip="testclient"
    )
    assert client.get("/admin/users/dave", headers=_BEARER).status_code == 404


def test_delete_user_404_unknown_audits_error(client):
    """Unknown user → 404 + audit status=error (single choke point)."""
    with mock.patch.object(admin, "log_admin_operation") as mock_admin_audit:
        resp = client.delete("/admin/users/ghost", headers=_BEARER)
    assert resp.status_code == 404
    assert "ghost" in _error_of(resp)
    mock_admin_audit.assert_called_once_with(
        action="delete_user", status="error", target_user="ghost", client_ip="testclient"
    )


def test_delete_user_400_transport_guard(client):
    """Control characters in the path username → 400 transport guard."""
    resp = client.delete("/admin/users/a%00b", headers=_BEARER)
    assert resp.status_code == 400
    assert "control" in _error_of(resp)


# ---------------------------------------------------------------------------
# 10. POST /admin/users/{username}/configs — grant (200 idempotent)
# ---------------------------------------------------------------------------


def test_grant_config_200_idempotent(client):
    """Grant → 200 user + restart_required; re-grant is a no-op → 200."""
    with mock.patch.object(admin, "log_admin_operation") as mock_admin_audit:
        resp = client.post(
            "/admin/users/bob/configs",
            json={"config": "default"},
            headers=_BEARER,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"username", "configs", "restart_required"}
    assert body["username"] == "bob"
    assert body["configs"] == ["default"]
    assert body["restart_required"] is True
    mock_admin_audit.assert_called_once_with(
        action="grant_config", status="ok", target_user="bob", client_ip="testclient"
    )
    # Idempotent re-grant → 200 with unchanged state.
    resp = client.post(
        "/admin/users/bob/configs",
        json={"config": "default"},
        headers=_BEARER,
    )
    assert resp.status_code == 200
    assert resp.json()["configs"] == ["default"]


def test_grant_config_404_unknown_user_or_config(client):
    """Unknown user and unknown config both → 404 (table (d))."""
    resp = client.post(
        "/admin/users/ghost/configs",
        json={"config": "default"},
        headers=_BEARER,
    )
    assert resp.status_code == 404
    assert "ghost" in _error_of(resp)

    resp = client.post(
        "/admin/users/alice/configs",
        json={"config": "nope"},
        headers=_BEARER,
    )
    assert resp.status_code == 404
    assert "nope" in _error_of(resp)


def test_grant_config_400_missing_or_non_string_field(client):
    """Missing/non-string ``config`` field → 400 (body shape is API-owned)."""
    for payload in ({}, {"config": 5}):
        resp = client.post("/admin/users/alice/configs", json=payload, headers=_BEARER)
        assert resp.status_code == 400, payload
        assert "config" in _error_of(resp), payload


def test_grant_config_400_passthrough_violation(client):
    """Second passthrough-reaching grant → 400 naming the offending user."""
    _add_passthrough_configs(os.environ["CALDAV_MCP_DB_PATH"])
    first = client.post(
        "/admin/users/alice/configs",
        json={"config": "pt-one"},
        headers=_BEARER,
    )
    assert first.status_code == 200
    resp = client.post(
        "/admin/users/alice/configs",
        json={"config": "pt-two"},
        headers=_BEARER,
    )
    assert resp.status_code == 400
    err = _error_of(resp)
    assert "passthrough" in err
    assert "'alice'" in err


# ---------------------------------------------------------------------------
# 11. DELETE /admin/users/{username}/configs/{config} — revoke (200 + body)
# ---------------------------------------------------------------------------


def test_revoke_config_200_idempotent_with_body(client):
    """Revoke → 200 {revoked, username, config, restart_required}; repeat → 200."""
    with mock.patch.object(admin, "log_admin_operation") as mock_admin_audit:
        resp = client.delete("/admin/users/alice/configs/default", headers=_BEARER)
    assert resp.status_code == 200
    assert resp.json() == {
        "revoked": True,
        "username": "alice",
        "config": "default",
        "restart_required": True,
    }
    mock_admin_audit.assert_called_once_with(
        action="revoke_config",
        status="ok",
        target_user="alice",
        client_ip="testclient",
    )
    # Revoking again (now never-granted) stays an idempotent no-op → 200.
    resp = client.delete("/admin/users/alice/configs/default", headers=_BEARER)
    assert resp.status_code == 200
    assert resp.json()["revoked"] is True
    # Never-granted config for a known user → also 200 (store parity).
    resp = client.delete("/admin/users/bob/configs/default", headers=_BEARER)
    assert resp.status_code == 200


def test_revoke_config_404_unknown_user_precheck(client):
    """Unknown user → 404 via the get_user pre-check — before config validation.

    The config name here would fail store charset validation (→ 400) if the
    handler reached ``revoke_config``; the mandated pre-check wins with 404.
    """
    with mock.patch.object(admin, "log_admin_operation") as mock_admin_audit:
        resp = client.delete("/admin/users/ghost/configs/a.b", headers=_BEARER)
    assert resp.status_code == 404
    assert "ghost" in _error_of(resp)
    mock_admin_audit.assert_called_once_with(
        action="revoke_config", status="error", target_user="ghost", client_ip="testclient"
    )


# ---------------------------------------------------------------------------
# 12. PUT /admin/users/{username}/configs — replace-all (200 / 404 / 409)
# ---------------------------------------------------------------------------


def test_replace_configs_200_replace_all(client):
    """Replace-all → 200 user + restart_required; duplicates deduped; [] clears."""
    with mock.patch.object(admin, "log_admin_operation") as mock_admin_audit:
        resp = client.put(
            "/admin/users/bob/configs",
            json={"configs": ["default", "default"]},
            headers=_BEARER,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"username", "configs", "restart_required"}
    assert body["configs"] == ["default"]  # set semantics: deduped
    assert body["restart_required"] is True
    mock_admin_audit.assert_called_once_with(
        action="set_user_configs",
        status="ok",
        target_user="bob",
        client_ip="testclient",
    )
    # Replace with the empty set → revoke-all.
    resp = client.put("/admin/users/bob/configs", json={"configs": []}, headers=_BEARER)
    assert resp.status_code == 200
    assert resp.json()["configs"] == []


def test_replace_configs_404_names_missing_configs_atomically(client):
    """Missing configs → 404 naming them; the pre-check blocks any write."""
    resp = client.put(
        "/admin/users/bob/configs",
        json={"configs": ["default", "ghost", "phantom"]},
        headers=_BEARER,
    )
    assert resp.status_code == 404
    err = _error_of(resp)
    assert "ghost" in err and "phantom" in err
    # Pre-check ran before set_user_configs: bob's grants are untouched.
    assert client.get("/admin/users/bob", headers=_BEARER).json()["configs"] == []


def test_replace_configs_404_unknown_user(client):
    """Unknown user → 404 (StoreNotFoundError from set_user_configs)."""
    resp = client.put(
        "/admin/users/ghost/configs",
        json={"configs": ["default"]},
        headers=_BEARER,
    )
    assert resp.status_code == 404
    assert "ghost" in _error_of(resp)


def test_replace_configs_400_bad_shapes(client):
    """Missing/non-list/non-string ``configs`` → 400 (API-owned shape)."""
    payloads = (
        {},  # missing field
        {"configs": "default"},  # not a list
        {"configs": [1]},  # non-string element
        {"configs": ["default", 5]},  # mixed list
    )
    for payload in payloads:
        resp = client.put("/admin/users/bob/configs", json=payload, headers=_BEARER)
        assert resp.status_code == 400, payload
        assert "configs" in _error_of(resp), payload


def test_replace_configs_400_passthrough_violation_rolls_back(client):
    """M1 in-store enforcement → 400; the transaction rollback preserves grants."""
    _add_passthrough_configs(os.environ["CALDAV_MCP_DB_PATH"])
    resp = client.put(
        "/admin/users/alice/configs",
        json={"configs": ["pt-one", "pt-two"]},
        headers=_BEARER,
    )
    assert resp.status_code == 400
    assert "passthrough" in _error_of(resp)
    # alice's original grant survived the rolled-back replace.
    assert client.get("/admin/users/alice", headers=_BEARER).json()["configs"] == ["default"]


def test_replace_configs_409_genuine_fk_race_only(client):
    """409 surfaces only from an FK race after the pre-check passed."""
    with mock.patch.object(
        ConfigStore,
        "set_user_configs",
        side_effect=StoreConflictError("config not found or other FK violation for user 'bob'"),
    ):
        resp = client.put(
            "/admin/users/bob/configs",
            json={"configs": ["default"]},
            headers=_BEARER,
        )
    assert resp.status_code == 409
    assert "config" in _error_of(resp)


# ---------------------------------------------------------------------------
# 13. Cross-cutting write status codes (table (d) / status-code policy)
# ---------------------------------------------------------------------------


def test_write_429_when_rate_limited(client, _admin_setup):
    """A limited IP is rejected with 429 + Retry-After before any store work."""
    limiter = _admin_setup
    for _ in range(3):
        limiter.record_failure("testclient")
    with mock.patch.object(admin, "ConfigStore") as mock_store:
        resp = client.post(
            "/admin/users",
            json={"username": "x", "key": "y"},
            headers=_BEARER,
        )
    assert resp.status_code == 429
    assert int(resp.headers["retry-after"]) >= 1
    mock_store.assert_not_called()


def test_write_404_fail_closed_when_admin_key_unset(client, _admin_setup, monkeypatch):
    """ADMIN_KEY empty → write routes 404 before any comparison (fail-closed)."""
    limiter = _admin_setup
    monkeypatch.setattr(config, "ADMIN_KEY", "")
    with mock.patch.object(admin, "_const_eq") as mock_const_eq:
        resp = client.post(
            "/admin/users",
            json={"username": "x", "key": "y"},
            headers=_BEARER,
        )
    assert resp.status_code == 404
    mock_const_eq.assert_not_called()
    assert limiter.is_rate_limited("testclient") is False


def test_write_404_in_simple_mode(client):
    """With the singleton in env (simple) mode write routes also 404."""
    env_app = AppConfig(mode="env", config=_FAKE_CONFIG)
    configure_app_config(env_app)
    resp = client.post("/admin/users", json={"username": "x", "key": "y"}, headers=_BEARER)
    assert resp.status_code == 404
    assert _error_of(resp) == "not found"
    resp = client.delete("/admin/users/alice", headers=_BEARER)
    assert resp.status_code == 404


def test_create_user_503_store_unavailable(client, tmp_path, monkeypatch):
    """Writes hit the same choke point: unreadable store → 503 + retry hint."""
    monkeypatch.setenv("CALDAV_MCP_DB_PATH", str(tmp_path))
    resp = client.post(
        "/admin/users",
        json={"username": "x", "key": "y"},
        headers=_BEARER,
    )
    assert resp.status_code == 503
    assert "retry" in _error_of(resp).lower()
