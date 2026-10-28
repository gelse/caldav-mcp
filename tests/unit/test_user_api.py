"""Unit tests for the user calendar provisioning route (``POST /user/calendars``).

**Test vehicle (pinned by the milestone):** Starlette ``TestClient`` against
the FastMCP HTTP app (``mcp.http_app()``) — same vehicle as
``tests/unit/test_admin_api.py``; the guard consumes a real ``Request`` and
route-level 404/405 semantics are part of acceptance.

Fixture (admin pattern plus the D7 extras): a per-test SQLite store with two
users, env patches for ``CALDAV_MCP_DB_PATH`` **and**
``CALDAV_MCP_CONFIG_SECRET`` (the live apply reads the secret at call time),
``configure_app_config``/``reset_app_config`` resets, a pro-user snapshot
seeded from the store's users (an empty snapshot would fail auth with "no
users configured" — pattern from ``test_auth_pro_mode.py``), and a fresh
``RateLimiter`` installed on ``caldav_mcp.auth.auth_rate_limiter`` (the
*shared* auth limiter, not admin's) yielded so tests can assert on
genuinely recorded failures.

Covers the plan's D7 matrix: success + store rows + rebuilt live state,
remote reuse / password refresh, collision & mismatch 409s, interrupted
chain retry, pre-write validation, auth branches (incl. rate-limit 401 and
the fail-closed ``pro_user is None``), non-pro 404, reload-failure signal,
secret-leak hygiene, audit, dotted-path addressing integration, and the
grant-revocation degradation path.

All tests are Docker-free.
"""

from __future__ import annotations

import json
import os
import sqlite3
from unittest import mock

import pytest
from starlette.testclient import TestClient

import caldav_mcp.auth as auth
import caldav_mcp.user_api as user_api
from caldav_mcp import mcp
from caldav_mcp.addressing import resolve_addressed_calendar
from caldav_mcp.app_config import (
    AppConfig,
    configure_app_config,
    find_config,
    find_remote,
    get_app_config,
    reset_app_config,
)
from caldav_mcp.auth import _FAILURE_MSG, configure_pro_users, get_pro_users, reset_pro_users
from caldav_mcp.config_store import ConfigStore, RemoteRecord
from caldav_mcp.db_loader import (
    USER_TOKEN_PREFIX,
    ProUser,
    _decrypt_with_secret,
    _encrypt_with_secret,
    load_pro_state,
)
from caldav_mcp.key_hash import hash_api_key
from caldav_mcp.rate_limit import RateLimiter
from caldav_mcp.user_api import configure_user_routes

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

_ALICE_KEY = "alice-unit-key"
_BOB_KEY = "bob-unit-key"
_MASTER_SECRET = "unit-test-master-secret"

_DAV_URL = "https://cal.example/dav"
_DAV_USERNAME = "alice-dav"
_DAV_PASSWORD = "s3cret-dav-pass"

_CONFIG = "user-alice"

_PRO_APP = AppConfig(mode="db", config=None, configs=())


def _auth_headers(username: str = "alice", key: str = _ALICE_KEY) -> dict[str, str]:
    """Pro-mode endpoint auth headers (``X-Mcp-Username`` + Bearer)."""
    return {"x-mcp-username": username, "authorization": f"Bearer {key}"}


def _payload(**overrides: object) -> dict[str, object]:
    """A valid ``POST /user/calendars`` body; *overrides* replace/add fields."""
    body: dict[str, object] = {
        "caldav_url": _DAV_URL,
        "caldav_username": _DAV_USERNAME,
        "caldav_password": _DAV_PASSWORD,
        "calendar_name": "work",
    }
    body.update(overrides)
    return body


def _error_of(response) -> str:
    """Return the ``error`` message of a ``{"error": ...}`` JSON body."""
    body = response.json()
    assert isinstance(body, dict) and isinstance(body.get("error"), str), body
    return body["error"]


def _db_path() -> str:
    return os.environ["CALDAV_MCP_DB_PATH"]


def _seed_pro_users(db_path: str) -> None:
    """Install the pro-user snapshot from the store (pattern: test_auth_pro_mode).

    Without this the snapshot is ``()`` and every request fails auth with
    "pro mode enabled but no users configured in the config store".
    """
    with ConfigStore(db_path) as store:
        records = store.list_users()
    configure_pro_users(
        tuple(
            ProUser(username=r.username, key_hash=r.key_hash, config_names=r.config_names)
            for r in records
        )
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _register_user_routes() -> None:
    """Register ``POST /user/calendars`` on the shared instance once per session."""
    configure_user_routes(mcp)


@pytest.fixture(autouse=True)
def _user_api_setup(tmp_path, monkeypatch):
    """Fresh store + env + pro-mode singleton + seeded snapshot + fresh limiter.

    Yields the fresh ``RateLimiter`` installed on ``caldav_mcp.auth`` so
    tests can assert on genuinely recorded failures (fixture pattern of
    ``test_auth_pro_mode.py``).  ``CALDAV_MCP_CONFIG_SECRET`` is set per
    test because the live apply reads it at call time.
    """
    db_path = str(tmp_path / "user-store.db")
    with ConfigStore(db_path) as store:
        store.create_user("alice", hash_api_key(_ALICE_KEY))
        store.create_user("bob", hash_api_key(_BOB_KEY))
    monkeypatch.setenv("CALDAV_MCP_DB_PATH", db_path)
    monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", _MASTER_SECRET)
    configure_app_config(_PRO_APP)
    _seed_pro_users(db_path)
    fresh = RateLimiter(max_failures=3, window_seconds=300)
    with mock.patch("caldav_mcp.auth.auth_rate_limiter", fresh):
        yield fresh
    reset_pro_users()
    reset_app_config()


@pytest.fixture(scope="module")
def client():
    """Module-scoped TestClient over the FastMCP HTTP app (routes registered)."""
    with TestClient(mcp.http_app()) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# 1. Success: 201 with exact body, store rows, rebuilt live state, snapshot
# ---------------------------------------------------------------------------


def test_success_201_creates_rows_and_live_state(client):
    """201: exact body, store rows (u1: token + grant), rebuilt AppConfig, snapshot."""
    resp = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc"),
        headers=_auth_headers(),
    )
    assert resp.status_code == 201
    # Exact body — proves the four path fields and NO "reload" key on success.
    assert resp.json() == {
        "path": "user-alice.nc.work",
        "config": "user-alice",
        "remote": "nc",
        "calendar": "work",
    }

    with ConfigStore(_db_path()) as store:
        assert store.list_configs() == (_CONFIG,)
        snapshot = store.load_config(_CONFIG)
        assert [(r.name, r.url, r.username, r.auth_mode) for r in snapshot.remotes] == [
            ("nc", _DAV_URL, _DAV_USERNAME, "direct")
        ]
        assert snapshot.remotes[0].password_enc.startswith(USER_TOKEN_PREFIX)
        assert [c.name for c in snapshot.calendars] == ["work"]
        assert store.list_config_grants(_CONFIG) == ("alice",)
        user_secret = store.get_user_secret("alice")

    # The stored ciphertext decrypts to the submitted password with the
    # caller's per-user secret (composition: u1: prefix + tagged token).
    stored = snapshot.remotes[0].password_enc
    assert _decrypt_with_secret(stored.removeprefix(USER_TOKEN_PREFIX), user_secret) == (
        _DAV_PASSWORD
    )

    # Live apply: the rebuilt singleton carries config + remote + calendar …
    app = get_app_config()
    cfg = find_config(app, _CONFIG)
    assert cfg is not None
    remote = find_remote(cfg, "nc")
    assert remote is not None
    assert (remote.url, remote.username, remote.password) == (
        _DAV_URL,
        _DAV_USERNAME,
        _DAV_PASSWORD,
    )
    assert [(rname, [c.name for c in cals]) for rname, cals in cfg.calendars] == [("nc", ["work"])]
    # … and the pro-user snapshot carries the new grant.
    users = {u.username: u for u in get_pro_users()}
    assert users["alice"].config_names == (_CONFIG,)
    assert users["bob"].config_names == ()


def test_remote_name_defaults_to_calendar_name(client):
    """Omitting remote_name defaults the remote (and thus the path) to calendar_name."""
    resp = client.post("/user/calendars", json=_payload(), headers=_auth_headers())
    assert resp.status_code == 201
    assert resp.json() == {
        "path": "user-alice.work.work",
        "config": "user-alice",
        "remote": "work",
        "calendar": "work",
    }


# ---------------------------------------------------------------------------
# 2. Remote reuse / password refresh
# ---------------------------------------------------------------------------


def test_second_call_same_remote_reuses_remote_row(client):
    """Same remote_name + same server → one remote row, two calendar rows."""
    first = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="work"),
        headers=_auth_headers(),
    )
    second = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="tasks"),
        headers=_auth_headers(),
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["path"] == "user-alice.nc.tasks"

    with ConfigStore(_db_path()) as store:
        snapshot = store.load_config(_CONFIG)
        assert [r.name for r in snapshot.remotes] == ["nc"]
        assert [c.name for c in snapshot.calendars] == ["work", "tasks"]


def test_reuse_with_changed_password_updates_token(client):
    """Same server, rotated password → new u1: token, 201, still one remote."""
    first = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="work"),
        headers=_auth_headers(),
    )
    assert first.status_code == 201
    with ConfigStore(_db_path()) as store:
        token_before = store.load_config(_CONFIG).remotes[0].password_enc

    resp = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="work2", caldav_password="rotated-pass"),
        headers=_auth_headers(),
    )
    assert resp.status_code == 201

    with ConfigStore(_db_path()) as store:
        snapshot = store.load_config(_CONFIG)
        assert [r.name for r in snapshot.remotes] == ["nc"]
        token_after = snapshot.remotes[0].password_enc
        user_secret = store.get_user_secret("alice")
    assert token_after != token_before  # Fernet embeds a fresh IV per write
    assert token_after.startswith(USER_TOKEN_PREFIX)
    assert _decrypt_with_secret(token_after.removeprefix(USER_TOKEN_PREFIX), user_secret) == (
        "rotated-pass"
    )


# ---------------------------------------------------------------------------
# 3. Remote conflicts → 409
# ---------------------------------------------------------------------------


def test_same_remote_name_different_url_409(client):
    """Same remote_name with a different URL → 409, never attach to the wrong server."""
    first = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="work"),
        headers=_auth_headers(),
    )
    assert first.status_code == 201
    resp = client.post(
        "/user/calendars",
        json=_payload(
            remote_name="nc",
            calendar_name="other",
            caldav_url="https://other.example/dav",
        ),
        headers=_auth_headers(),
    )
    assert resp.status_code == 409
    assert "choose another remote_name" in _error_of(resp)
    with ConfigStore(_db_path()) as store:
        snapshot = store.load_config(_CONFIG)
        assert snapshot.remotes[0].url == _DAV_URL  # unchanged
        assert [c.name for c in snapshot.calendars] == ["work"]  # no write happened


def test_same_remote_name_different_username_409(client):
    """Same remote_name + URL but a different CalDAV username → 409."""
    first = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="work"),
        headers=_auth_headers(),
    )
    assert first.status_code == 201
    resp = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="other", caldav_username="mallory"),
        headers=_auth_headers(),
    )
    assert resp.status_code == 409
    assert "choose another remote_name" in _error_of(resp)


def test_undecryptable_stored_token_409(client):
    """A stored token the caller's secret cannot decrypt → 409 (no silent reuse)."""
    first = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="work"),
        headers=_auth_headers(),
    )
    assert first.status_code == 201
    # Corrupt the stored ciphertext (valid tag, undecryptable payload).
    with ConfigStore(_db_path()) as store:
        snapshot = store.load_config(_CONFIG)
        store.update_remote(
            _CONFIG,
            "nc",
            RemoteRecord(
                config_name=_CONFIG,
                name="nc",
                url=snapshot.remotes[0].url,
                auth_mode="direct",
                username=snapshot.remotes[0].username,
                password_enc=f"{USER_TOKEN_PREFIX}AAAA",
            ),
        )
    resp = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="work2"),
        headers=_auth_headers(),
    )
    assert resp.status_code == 409
    assert "choose another remote_name" in _error_of(resp)
    with ConfigStore(_db_path()) as store:
        assert [c.name for c in store.load_config(_CONFIG).calendars] == ["work"]


def test_duplicate_calendar_409(client):
    """Identical second POST → genuine duplicate calendar 409; one row remains."""
    first = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="work"),
        headers=_auth_headers(),
    )
    assert first.status_code == 201
    resp = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="work"),
        headers=_auth_headers(),
    )
    assert resp.status_code == 409
    assert "already exists" in _error_of(resp)
    with ConfigStore(_db_path()) as store:
        snapshot = store.load_config(_CONFIG)
        assert [r.name for r in snapshot.remotes] == ["nc"]
        assert [c.name for c in snapshot.calendars] == ["work"]


# ---------------------------------------------------------------------------
# 4. Interrupted chain retry + cross-user config collision
# ---------------------------------------------------------------------------


def test_interrupted_chain_retry_completes_201(client):
    """Config+grant+remote present, calendar missing → 201 completes the chain."""
    with ConfigStore(_db_path()) as store:
        store.create_config(_CONFIG)
        store.grant_config("alice", _CONFIG)
        user_secret = store.get_user_secret("alice")
        seeded_token = USER_TOKEN_PREFIX + _encrypt_with_secret(_DAV_PASSWORD, user_secret)
        store.create_remote(
            _CONFIG,
            RemoteRecord(
                config_name=_CONFIG,
                name="nc",
                url=_DAV_URL,
                auth_mode="direct",
                username=_DAV_USERNAME,
                password_enc=seeded_token,
            ),
        )

    resp = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="work"),
        headers=_auth_headers(),
    )
    assert resp.status_code == 201
    assert resp.json()["path"] == "user-alice.nc.work"
    with ConfigStore(_db_path()) as store:
        snapshot = store.load_config(_CONFIG)
        # Remote reused untouched (same password → token byte-identical) …
        assert snapshot.remotes[0].password_enc == seeded_token
        assert [c.name for c in snapshot.calendars] == ["work"]
        # … and the idempotent grant did not duplicate anything.
        assert store.list_config_grants(_CONFIG) == ("alice",)


def test_collision_config_granted_to_other_user_409(client):
    """Personal config already granted to another username → 409, no writes."""
    with ConfigStore(_db_path()) as store:
        store.create_config(_CONFIG)
        store.grant_config("bob", _CONFIG)

    resp = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="work"),
        headers=_auth_headers(),
    )
    assert resp.status_code == 409
    assert "another user" in _error_of(resp)
    with ConfigStore(_db_path()) as store:
        # The other user's grant stands; the caller gained nothing.
        assert store.list_config_grants(_CONFIG) == ("bob",)
        snapshot = store.load_config(_CONFIG)
        assert snapshot.remotes == ()
        assert snapshot.calendars == ()


# ---------------------------------------------------------------------------
# 5. Pre-write validation → 400 (never a store write)
# ---------------------------------------------------------------------------


def test_missing_empty_or_non_str_fields_400(client):
    """Missing/empty/non-string required fields and bad remote_name → 400, no rows."""
    base = _payload()
    cases: list[tuple[str, dict[str, object]]] = []
    missing_url = dict(base)
    missing_url.pop("caldav_url")
    cases.append(("missing caldav_url", missing_url))
    empty_password = dict(base)
    empty_password["caldav_password"] = ""
    cases.append(("empty caldav_password", empty_password))
    non_str_username = dict(base)
    non_str_username["caldav_username"] = 42
    cases.append(("non-str caldav_username", non_str_username))
    null_calendar = dict(base)
    null_calendar["calendar_name"] = None
    cases.append(("null calendar_name", null_calendar))
    non_str_remote = dict(base)
    non_str_remote["remote_name"] = 7
    cases.append(("non-str remote_name", non_str_remote))
    empty_remote = dict(base)
    empty_remote["remote_name"] = ""
    cases.append(("empty remote_name", empty_remote))

    for label, body in cases:
        resp = client.post("/user/calendars", json=body, headers=_auth_headers())
        assert resp.status_code == 400, (label, resp.status_code, resp.text)
        _error_of(resp)

    with ConfigStore(_db_path()) as store:
        assert store.list_configs() == ()  # nothing was ever created


@pytest.mark.parametrize("calendar_name", ["work.v2", "work v2"])
def test_bad_calendar_name_400_before_any_store_write(client, calendar_name):
    """Dot/space in calendar_name → 400 (store's message) with NO config row."""
    resp = client.post(
        "/user/calendars", json=_payload(calendar_name=calendar_name), headers=_auth_headers()
    )
    assert resp.status_code == 400
    error = _error_of(resp)
    assert "calendar name" in error  # the store's own _validate_name message
    with ConfigStore(_db_path()) as store:
        assert store.list_configs() == ()


def test_invalid_url_400(client):
    """Non-http scheme / missing host / bare host path → 400 before any write."""
    for url in ("ftp://cal.example/dav", "https://", "cal.example/dav"):
        resp = client.post(
            "/user/calendars", json=_payload(caldav_url=url), headers=_auth_headers()
        )
        assert resp.status_code == 400, (url, resp.status_code, resp.text)
        assert "caldav_url" in _error_of(resp)
    with ConfigStore(_db_path()) as store:
        assert store.list_configs() == ()


def test_malformed_json_400(client):
    """Malformed JSON body → 400."""
    resp = client.post(
        "/user/calendars",
        data="{not json",
        headers={"content-type": "application/json", **_auth_headers()},
    )
    assert resp.status_code == 400
    assert "malformed" in _error_of(resp)


def test_wrong_content_type_400(client):
    """Non-JSON Content-Type → 400 before the body is parsed."""
    resp = client.post(
        "/user/calendars",
        data=json.dumps(_payload()),
        headers={"content-type": "text/plain", **_auth_headers()},
    )
    assert resp.status_code == 400
    assert "Content-Type" in _error_of(resp)


def test_non_object_body_400(client):
    """A JSON array body → 400 (object body required)."""
    resp = client.post("/user/calendars", json=[1, 2], headers=_auth_headers())
    assert resp.status_code == 400
    assert "JSON object" in _error_of(resp)


# ---------------------------------------------------------------------------
# 6. Auth branches → 401 (incl. rate-limit retry hint and fail-closed None)
# ---------------------------------------------------------------------------


def test_missing_key_401(client):
    """No credentials → 401 with the standard auth failure message."""
    resp = client.post("/user/calendars", json=_payload())
    assert resp.status_code == 401
    assert _error_of(resp) == _FAILURE_MSG


def test_wrong_key_401(client):
    """Valid username, wrong key → 401."""
    resp = client.post("/user/calendars", json=_payload(), headers=_auth_headers(key="wrong-key"))
    assert resp.status_code == 401
    assert _error_of(resp) == _FAILURE_MSG


def test_unknown_user_401(client):
    """Unknown X-Mcp-Username → 401 (no enumeration)."""
    resp = client.post("/user/calendars", json=_payload(), headers=_auth_headers(username="ghost"))
    assert resp.status_code == 401
    assert _error_of(resp) == _FAILURE_MSG


def test_rate_limited_401_carries_retry_hint(client, _user_api_setup):
    """Rate-limited caller → 401 (not admin's 429) with the limiter's retry hint.

    The limiter lives inside ``auth._authenticate`` (shared
    ``auth_rate_limiter``), so the route must not re-check it — the typed
    AUTH failure message already carries "retry in Ns".
    """
    limiter = _user_api_setup
    # auth's IP helper only sees the authorization header, so the TestClient
    # bucket key is always "unknown".
    for _ in range(3):
        limiter.record_failure("unknown")
    assert limiter.is_rate_limited("unknown")

    resp = client.post("/user/calendars", json=_payload(), headers=_auth_headers())
    assert resp.status_code == 401
    error = _error_of(resp)
    assert "rate limited" in error
    assert "retry in" in error


def test_pro_user_none_401_fail_closed(client):
    """Successful verify + vanished snapshot entry (``_find_matched_pro_user``
    returns None) → 401, never fail-open."""
    with mock.patch.object(auth, "_find_matched_pro_user", return_value=None):
        resp = client.post("/user/calendars", json=_payload(), headers=_auth_headers())
    assert resp.status_code == 401
    assert _error_of(resp) == _FAILURE_MSG


# ---------------------------------------------------------------------------
# 7. Non-pro guard → 404; wrong method → 405
# ---------------------------------------------------------------------------


def test_non_pro_mode_404(client):
    """Outside pro mode the guard 404s fail-closed, before auth and body parse."""
    configure_app_config(AppConfig(mode="header", config=None))
    resp = client.post("/user/calendars", json=_payload())  # no auth headers either
    assert resp.status_code == 404
    assert resp.json() == {"error": "not found"}


def test_wrong_method_405(client):
    """Only POST is registered on the route → 405 for other methods."""
    resp = client.get("/user/calendars", headers=_auth_headers())
    assert resp.status_code == 405


# ---------------------------------------------------------------------------
# 8. Live apply: reload-failure signal (write stands)
# ---------------------------------------------------------------------------


def test_reload_failure_201_reports_failed_and_write_stands(client):
    """Live apply raising → 201 + "reload": "failed"; store rows stand; detail logged."""
    with (
        mock.patch.object(user_api, "load_pro_state", side_effect=RuntimeError("loader exploded")),
        mock.patch.object(user_api, "log") as mock_log,
    ):
        resp = client.post(
            "/user/calendars",
            json=_payload(remote_name="nc", calendar_name="work"),
            headers=_auth_headers(),
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["reload"] == "failed"
    assert set(body) == {"path", "config", "remote", "calendar", "reload"}
    assert "loader exploded" not in resp.text  # detail stays in the log
    mock_log.error.assert_called_once()
    assert "live apply failed" in str(mock_log.error.call_args)

    # The write stands: config, remote, calendar and grant all exist.
    with ConfigStore(_db_path()) as store:
        assert store.list_configs() == (_CONFIG,)
        snapshot = store.load_config(_CONFIG)
        assert [r.name for r in snapshot.remotes] == ["nc"]
        assert [c.name for c in snapshot.calendars] == ["work"]
        assert store.list_config_grants(_CONFIG) == ("alice",)


# ---------------------------------------------------------------------------
# 9. Secret-leak hygiene + audit
# ---------------------------------------------------------------------------


def test_no_password_or_secret_leak_in_response(client):
    """The response never carries the CalDAV password or the user's enc_secret."""
    password = "leaky-password-XYZ-987"
    resp = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="work", caldav_password=password),
        headers=_auth_headers(),
    )
    assert resp.status_code == 201
    with ConfigStore(_db_path()) as store:
        password_enc = store.load_config(_CONFIG).remotes[0].password_enc
        enc_secret = store.get_user_secret("alice")
    assert password_enc.startswith(USER_TOKEN_PREFIX)
    assert password not in resp.text
    assert enc_secret not in resp.text


def test_audit_user_add_calendar_ok(client):
    """Exactly one user_add_calendar audit entry with status ok on success."""
    with mock.patch.object(user_api, "log_admin_operation") as mock_audit:
        resp = client.post(
            "/user/calendars",
            json=_payload(remote_name="nc", calendar_name="work"),
            headers=_auth_headers(),
        )
    assert resp.status_code == 201
    mock_audit.assert_called_once_with(
        action="user_add_calendar",
        status="ok",
        target_user="alice",
        client_ip="testclient",
    )


# ---------------------------------------------------------------------------
# 10. Addressing integration + grant-revocation degradation
# ---------------------------------------------------------------------------


def test_addressing_resolves_returned_path(client):
    """resolve_addressed_calendar() resolves the returned dotted path live."""
    resp = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="work"),
        headers=_auth_headers(),
    )
    assert resp.status_code == 201
    path = resp.json()["path"]
    user = {u.username: u for u in get_pro_users()}["alice"]
    resolution = resolve_addressed_calendar(get_app_config(), user, path)
    assert resolution.remote.url == _DAV_URL
    assert resolution.remote.username == _DAV_USERNAME
    assert resolution.calendar_name == "work"


def test_grant_removal_drops_remote_and_path_generic_error(client):
    """Grant rows deleted + reload → remote dropped from AppConfig; path
    resolves to the generic unknown-path error (never a crash)."""
    resp = client.post(
        "/user/calendars",
        json=_payload(remote_name="nc", calendar_name="work"),
        headers=_auth_headers(),
    )
    assert resp.status_code == 201
    path = resp.json()["path"]

    # Delete the grant rows directly, then trigger the same reload the live
    # apply performs.
    conn = sqlite3.connect(_db_path())
    try:
        conn.execute("DELETE FROM user_configs")
        conn.commit()
    finally:
        conn.close()
    state = load_pro_state(_db_path(), os.environ["CALDAV_MCP_CONFIG_SECRET"].strip())
    configure_app_config(state.app_config)
    configure_pro_users(state.users)

    # The config row survives, but with zero candidate user secrets the
    # per-user-encrypted remote is dropped from the loaded AppConfig.
    app = get_app_config()
    cfg = find_config(app, _CONFIG)
    assert cfg is not None
    assert find_remote(cfg, "nc") is None

    # Fail-closed generic error for the (now ungranted) user …
    user = {u.username: u for u in get_pro_users()}["alice"]
    with pytest.raises(ValueError, match="unknown calendar path"):
        resolve_addressed_calendar(app, user, path)
    # … and for an unfiltered caller: the remote itself is gone.
    with pytest.raises(ValueError, match="unknown calendar path"):
        resolve_addressed_calendar(app, None, path)
