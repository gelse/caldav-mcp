"""Admin API integration tests (M2.3 read cases + M3.1 write cases).

Requires the compose test stack from ``docker-compose.test.yaml`` (the
``make test-integration`` target builds the pro store and starts it):

* ``mcp-pro`` (http://localhost:8080) — ``DB_CONFIG_ENABLED=true`` **and**
  ``CALDAV_MCP_ADMIN_KEY=admin-test-key`` → admin routes registered; the
  store mount is read-write (M3.1) so container-side writes must return
  201/200 — **never 503** — proving the mount+chmod mechanism holds
  regardless of the host uid.
* ``mcp-simple`` (http://localhost:8081) — no DB mode → the admin API does
  not exist (framework 404).

Write cases use **disposable ``admin-e2e-*`` users only**: created via the
API, deleted again in ``finally`` — ``alice``/``bob`` are never mutated
(milestone edge-case table, ``conftest_pro.py:206-215``).

Because these tests mutate the bind-mounted store, manual runs of the
README flow must re-run ``build_pro_store.py`` first; ``make test-integration``
rebuilds the store automatically.

The restart-window proof issues an explicit ``docker compose restart
mcp-pro`` plus a container-health poll (no fixed sleeps) so it fits the
60 s per-test timeout (``Makefile:44``); it restarts once more first for a
clean baseline (see ``TestAdminRestartWindow`` for why).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_PRO_URL = os.environ.get("MCP_PRO_URL", "http://localhost:8080")
_SIMPLE_URL = os.environ.get("MCP_SIMPLE_URL", "http://localhost:8081")

# Must match CALDAV_MCP_ADMIN_KEY on the mcp-pro service in docker-compose.test.yaml.
_ADMIN_KEY = "admin-test-key"

_ADMIN_HEADERS = {"Authorization": f"Bearer {_ADMIN_KEY}"}

# Host-side paths: the bind-mounted store the mcp-pro container writes to.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STORE_PATH = _PROJECT_ROOT / "tests" / "integration" / "pro-store" / "pro-store.db"

# MCP streamable-HTTP endpoint + container used by the restart-window proof.
_MCP_URL = f"{_PRO_URL}/mcp"
_PRO_CONTAINER = "caldav-mcp-pro-test"
_MCP_REQ_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


# ---------------------------------------------------------------------------
# Helpers: admin API (create/delete/…), MCP auth probe, CLI, restart+health
# ---------------------------------------------------------------------------


def _delete_user_best_effort(username: str) -> None:
    """Cleanup for ``finally`` blocks — best effort, assertions live in the body."""
    requests.delete(f"{_PRO_URL}/admin/users/{username}", headers=_ADMIN_HEADERS, timeout=10)


def _run_config_cli(*args: str) -> subprocess.CompletedProcess:
    """Run the store CLI on the host against the bind-mounted pro store."""
    return subprocess.run(
        [sys.executable, "-m", "caldav_mcp.config_cli", "--db", str(_STORE_PATH), *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _mcp_initialize(session: requests.Session) -> None:
    """MCP ``initialize`` handshake; installs the session id on *session*."""
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "admin-integration-test", "version": "0.1.0"},
        },
    }
    resp = session.post(_MCP_URL, json=init_payload, headers=_MCP_REQ_HEADERS)
    resp.raise_for_status()
    session_id = resp.headers.get("mcp-session-id", "")
    if session_id:
        session.headers["mcp-session-id"] = session_id
    notif_payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    session.post(_MCP_URL, json=notif_payload, headers=_MCP_REQ_HEADERS)


def _parse_rpc_response(resp: requests.Response) -> dict[str, object]:
    """Parse a JSON-RPC response that may arrive as an SSE ``data:`` frame."""
    content_type = resp.headers.get("content-type", "")
    if content_type.startswith("text/event-stream"):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[len("data:") :].strip())
                assert isinstance(payload, dict), f"Expected dict payload, got {payload!r}"
                return payload
        raise AssertionError(f"No 'data:' frame in SSE response: {resp.text!r}")
    payload = resp.json()
    assert isinstance(payload, dict), f"Expected dict payload, got {payload!r}"
    return payload


def _mcp_auth_probe(username: str, key: str) -> tuple[str, str]:
    """Call ``caldav_list_calendars`` as *username*; return ``(status, message)``.

    Rejection observable: the MCP endpoint authenticates at the **tool**
    layer — ``auth.py:_require_auth_db_user`` returns
    ``ToolResult.failure(Status.AUTH, …)`` inside a 200 JSON-RPC response;
    ``/mcp`` has no HTTP-401 mapping (see the "no HTTP 401/403 → auth
    mapping" note in ``test_multi_remote_fanout.py``).  The typed
    ``status == "auth"`` plus the standard unauthorized message is therefore
    the rejection signal the restart-window proof asserts.
    """
    with requests.Session() as session:
        _mcp_initialize(session)
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "caldav_list_calendars", "arguments": {}},
        }
        headers = {
            **_MCP_REQ_HEADERS,
            "X-Mcp-Username": username,
            "Authorization": f"Bearer {key}",
        }
        resp = session.post(_MCP_URL, json=payload, headers=headers)
        resp.raise_for_status()
        rpc = _parse_rpc_response(resp)

    result = rpc.get("result")
    assert isinstance(result, dict), f"Expected result dict, got {rpc!r}"
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return str(structured.get("status", "")), str(structured.get("message", ""))
    content = result.get("content")
    assert isinstance(content, list) and content, f"Empty tool content: {rpc!r}"
    text_payload = json.loads(str(content[0].get("text", "")))
    assert isinstance(text_payload, dict), f"Expected dict text payload, got {text_payload!r}"
    return str(text_payload.get("status", "")), str(text_payload.get("message", ""))


def _mcp_auth_probe_retry(
    username: str, key: str, attempts: int = 3, delay_seconds: float = 1.0
) -> tuple[str, str]:
    """Probe with a short retry for transient connection errors (post-restart)."""
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return _mcp_auth_probe(username, key)
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(delay_seconds)
    raise AssertionError(f"MCP auth probe failed after {attempts} attempts: {last_error}")


def _restart_pro_and_wait_healthy() -> None:
    """``docker compose restart mcp-pro`` + efficient container-health poll.

    Polls ``.State.Health.Status`` (compose healthcheck: interval 5 s) every
    0.5 s — no fixed sleep — so the whole sequence fits the 60 s per-test
    timeout (``Makefile:44``).
    """
    subprocess.run(
        ["docker", "compose", "-f", "docker-compose.test.yaml", "restart", "mcp-pro"],
        cwd=_PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Health.Status}}",
                _PRO_CONTAINER,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip() == "healthy":
            return
        time.sleep(0.5)
    raise AssertionError("mcp-pro did not report healthy within 30s after restart")


# ---------------------------------------------------------------------------
# Pro mode: 200 with admin key / 401 without
# ---------------------------------------------------------------------------


class TestAdminReadProMode:
    """``GET /admin/users…`` on the pro-mode service."""

    def test_list_users_200_with_admin_key(self) -> None:
        """Valid admin key → 200 with store users; no key material exposed."""
        resp = requests.get(f"{_PRO_URL}/admin/users", headers=_ADMIN_HEADERS, timeout=10)
        assert resp.status_code == 200
        users = resp.json()["users"]
        names = {u["username"] for u in users}
        assert {"alice", "bob"} <= names, f"Expected store users in {names}"
        for user in users:
            assert set(user) == {"username", "configs"}
            assert isinstance(user["configs"], list)
        assert "key_hash" not in resp.text and '"key"' not in resp.text

    def test_get_user_200_with_admin_key(self) -> None:
        """Valid admin key + known user → 200 with the {username, configs} object."""
        resp = requests.get(f"{_PRO_URL}/admin/users/alice", headers=_ADMIN_HEADERS, timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        assert body["username"] == "alice"
        assert isinstance(body["configs"], list)
        assert set(body) == {"username", "configs"}
        assert "key_hash" not in resp.text and '"key"' not in resp.text

    def test_list_users_401_without_key(self) -> None:
        """No admin key → 401 with an error body."""
        resp = requests.get(f"{_PRO_URL}/admin/users", timeout=10)
        assert resp.status_code == 401
        assert "error" in resp.json()

    def test_get_user_401_with_wrong_key(self) -> None:
        """Wrong admin key → 401 with an error body."""
        resp = requests.get(
            f"{_PRO_URL}/admin/users/alice",
            headers={"Authorization": "Bearer definitely-not-the-key"},
            timeout=10,
        )
        assert resp.status_code == 401
        assert "error" in resp.json()


# ---------------------------------------------------------------------------
# Pro mode: write routes (M3.1) — container-side 201/200, never 503
# ---------------------------------------------------------------------------


class TestAdminWriteProMode:
    """``POST``/``DELETE``/grant-revoke-replace on the pro-mode service.

    Every assertion of 201/200 here doubles as the mount+permissions proof:
    with ``:ro`` dropped and the deterministic chmod in ``build_pro_store.py``
    missing, these container-side writes would surface ``sqlite3.
    OperationalError`` → 503 instead.
    """

    def test_create_get_delete_user_lifecycle(self) -> None:
        """Create → 201, duplicate → 409, GET sees it, DELETE → 200 + body → 404."""
        username = "admin-e2e-user"
        try:
            resp = requests.post(
                f"{_PRO_URL}/admin/users",
                json={"username": username, "key": "admin-e2e-user-key"},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            # 201 — NOT 503: the bind-mounted store is writable by `app`.
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert set(body) == {"username", "configs", "restart_required"}
            assert body["username"] == username
            assert body["configs"] == []
            assert body["restart_required"] is True
            # No key material echoed or returned (Design (f) hygiene).
            assert "admin-e2e-user-key" not in resp.text
            assert "key_hash" not in resp.text

            dup = requests.post(
                f"{_PRO_URL}/admin/users",
                json={"username": username, "key": "admin-e2e-user-key"},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert dup.status_code == 409, dup.text

            got = requests.get(
                f"{_PRO_URL}/admin/users/{username}", headers=_ADMIN_HEADERS, timeout=10
            )
            assert got.status_code == 200
            assert got.json() == {"username": username, "configs": []}
            assert "admin-e2e-user-key" not in got.text

            deleted = requests.delete(
                f"{_PRO_URL}/admin/users/{username}", headers=_ADMIN_HEADERS, timeout=10
            )
            assert deleted.status_code == 200, deleted.text
            assert deleted.json() == {
                "deleted": True,
                "username": username,
                "restart_required": True,
            }
            gone = requests.get(
                f"{_PRO_URL}/admin/users/{username}", headers=_ADMIN_HEADERS, timeout=10
            )
            assert gone.status_code == 404
        finally:
            _delete_user_best_effort(username)

    def test_write_validation_and_auth_statuses(self) -> None:
        """401 without key; 400 for missing key and malformed JSON (table (d))."""
        resp = requests.post(
            f"{_PRO_URL}/admin/users",
            json={"username": "admin-e2e-nokey-attempt", "key": "k"},
            timeout=10,
        )
        assert resp.status_code == 401
        assert "error" in resp.json()

        resp = requests.post(
            f"{_PRO_URL}/admin/users",
            json={"username": "admin-e2e-nokey"},
            headers=_ADMIN_HEADERS,
            timeout=10,
        )
        assert resp.status_code == 400
        assert "key" in resp.json()["error"]

        resp = requests.post(
            f"{_PRO_URL}/admin/users",
            data="{not-json",
            headers={**_ADMIN_HEADERS, "Content-Type": "application/json"},
            timeout=10,
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_delete_unknown_user_404(self) -> None:
        """DELETE for a user missing from the store → 404."""
        resp = requests.delete(
            f"{_PRO_URL}/admin/users/admin-e2e-ghost",
            headers=_ADMIN_HEADERS,
            timeout=10,
        )
        assert resp.status_code == 404
        assert "admin-e2e-ghost" in resp.json()["error"]

    def test_username_transport_guard_400(self) -> None:
        """Control characters in the path username → 400 transport guard."""
        resp = requests.delete(f"{_PRO_URL}/admin/users/a%00b", headers=_ADMIN_HEADERS, timeout=10)
        assert resp.status_code == 400
        assert "control" in resp.json()["error"]

    def test_grant_replace_revoke_lifecycle(self) -> None:
        """Grant (idempotent) → replace-all → 404 naming missing → revoke (idempotent)."""
        username = "admin-e2e-grants"
        try:
            created = requests.post(
                f"{_PRO_URL}/admin/users",
                json={"username": username, "key": "admin-e2e-grants-key"},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert created.status_code == 201, created.text

            grant = requests.post(
                f"{_PRO_URL}/admin/users/{username}/configs",
                json={"config": "main"},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert grant.status_code == 200, grant.text
            assert grant.json()["configs"] == ["main"]
            assert grant.json()["restart_required"] is True

            again = requests.post(
                f"{_PRO_URL}/admin/users/{username}/configs",
                json={"config": "main"},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert again.status_code == 200
            assert again.json()["configs"] == ["main"]  # idempotent

            replaced = requests.put(
                f"{_PRO_URL}/admin/users/{username}/configs",
                json={"configs": ["second"]},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert replaced.status_code == 200, replaced.text
            assert replaced.json()["configs"] == ["second"]
            assert replaced.json()["restart_required"] is True

            # Pre-check 404 names the missing config; existing grants untouched.
            missing = "admin-e2e-no-such-config"
            bad = requests.put(
                f"{_PRO_URL}/admin/users/{username}/configs",
                json={"configs": ["main", missing]},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert bad.status_code == 404, bad.text
            assert missing in bad.json()["error"]
            unchanged = requests.get(
                f"{_PRO_URL}/admin/users/{username}",
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert unchanged.json()["configs"] == ["second"]

            revoked = requests.delete(
                f"{_PRO_URL}/admin/users/{username}/configs/second",
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert revoked.status_code == 200, revoked.text
            assert revoked.json() == {
                "revoked": True,
                "username": username,
                "config": "second",
                "restart_required": True,
            }
            # Revoking again (never-granted now) → 200 idempotent no-op.
            revoked_again = requests.delete(
                f"{_PRO_URL}/admin/users/{username}/configs/second",
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert revoked_again.status_code == 200
        finally:
            _delete_user_best_effort(username)

    def test_grant_404_unknown_user(self) -> None:
        """Grant for an unknown user → 404 (store get_user/grant pre-check)."""
        resp = requests.post(
            f"{_PRO_URL}/admin/users/admin-e2e-ghost/configs",
            json={"config": "main"},
            headers=_ADMIN_HEADERS,
            timeout=10,
        )
        assert resp.status_code == 404
        assert "admin-e2e-ghost" in resp.json()["error"]

    def test_api_created_user_visible_to_cli(self) -> None:
        """Cross-surface: CLI ``user list`` / ``user show`` see API-created users."""
        username = "admin-e2e-cli"
        try:
            created = requests.post(
                f"{_PRO_URL}/admin/users",
                json={"username": username, "key": "admin-e2e-cli-key"},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert created.status_code == 201, created.text
            granted = requests.post(
                f"{_PRO_URL}/admin/users/{username}/configs",
                json={"config": "main"},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert granted.status_code == 200, granted.text

            listed = _run_config_cli("user", "list")
            assert listed.returncode == 0, listed.stderr
            assert username in listed.stdout.splitlines(), listed.stdout

            shown = _run_config_cli("user", "show", "--username", username)
            assert shown.returncode == 0, shown.stderr
            lines = shown.stdout.splitlines()
            assert username in lines, shown.stdout
            assert "config: main" in lines, shown.stdout
        finally:
            _delete_user_best_effort(username)


# ---------------------------------------------------------------------------
# Restart-window proof (M3.1) — ONE dedicated test + efficient health poll
# ---------------------------------------------------------------------------


class TestAdminRestartWindow:
    """Restart-to-apply semantics for a user created via the admin API."""

    def test_created_user_rejected_until_restart_then_accepted(self) -> None:
        """Create → 201 → MCP auth rejected pre-restart → restart → accepted.

        The pre-restart rejection is the typed AUTH failure probed by
        ``_mcp_auth_probe`` (the ``/mcp`` endpoint has no HTTP-401 mapping —
        rejection is tool-level, ``auth.py:257-327``).  The restart step is
        explicit (``docker compose restart mcp-pro``) with a container-health
        poll so the whole sequence fits the 60 s per-test timeout.

        The proof is bracketed by an extra baseline restart first: (1) a
        previous run's ``finally`` deletes the disposable user from the
        store, but the running container keeps authenticating it from its
        startup snapshot until the next restart (the very restart-to-apply
        semantics under test), so a warm-stack re-run of this test must
        reload the snapshot before the pre-restart rejection means anything;
        (2) failed MCP auths pile into the single shared fallback bucket
        (``auth.py`` client-IP quirk, see ``test_pro_mode.py:424``) and the
        rate-limit check runs before credential verification, so a stale
        hot bucket would produce a rate-limit rejection — also ``"auth"``
        but vacuous as snapshot evidence.  A fresh process resets both.
        """
        username = "admin-e2e-restart"
        key = "admin-e2e-restart-key"
        try:
            # Baseline restart: pristine startup snapshot + in-memory auth
            # rate limiter (see docstring) before the proof window opens.
            _restart_pro_and_wait_healthy()

            created = requests.post(
                f"{_PRO_URL}/admin/users",
                json={"username": username, "key": key},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert created.status_code == 201, created.text
            assert created.json()["restart_required"] is True

            # Pre-restart: absent from the startup snapshot → rejected.
            status, message = _mcp_auth_probe_retry(username, key)
            assert status == "auth", (status, message)
            assert "unauthorized" in message.lower(), message

            # Explicit restart + efficient health wait (no fixed sleep).
            _restart_pro_and_wait_healthy()

            # Post-restart: snapshot reloaded from the store → accepted.
            status, message = _mcp_auth_probe_retry(username, key)
            assert status in ("ok", "empty"), (status, message)
            assert "unauthorized" not in message.lower(), message
        finally:
            _delete_user_best_effort(username)


# ---------------------------------------------------------------------------
# Simple mode: admin API does not exist → 404
# ---------------------------------------------------------------------------


class TestAdminAbsentInSimpleMode:
    """The simple-mode service never registers the admin routes."""

    def test_list_users_404_simple_mode(self) -> None:
        """GET /admin/users on mcp-simple → 404 (even with a key presented)."""
        resp = requests.get(f"{_SIMPLE_URL}/admin/users", headers=_ADMIN_HEADERS, timeout=10)
        assert resp.status_code == 404

    def test_get_user_404_simple_mode(self) -> None:
        """GET /admin/users/{username} on mcp-simple → 404."""
        resp = requests.get(f"{_SIMPLE_URL}/admin/users/alice", headers=_ADMIN_HEADERS, timeout=10)
        assert resp.status_code == 404

    def test_create_user_404_simple_mode(self) -> None:
        """POST /admin/users on mcp-simple → 404 (write routes never registered)."""
        resp = requests.post(
            f"{_SIMPLE_URL}/admin/users",
            json={"username": "x", "key": "y"},
            headers=_ADMIN_HEADERS,
            timeout=10,
        )
        assert resp.status_code == 404
