"""Admin API integration tests (M2.3 read cases + M3/M4 write cases + M5.1 live apply).

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
(milestone edge-case table, ``conftest_pro.py:206-215``).  The one
exception is the dedicated, never-granted ``admin-snapshot-user`` (seeded
by ``conftest_pro._build_pro_store``): the rotation proof rotates its key
and restores it in ``finally`` — it is never deleted.

Because these tests mutate the bind-mounted store, manual runs of the
README flow must re-run ``build_pro_store.py`` first; ``make test-integration``
rebuilds the store automatically.

**M5.1 live round-trips** (they replace M3's restart-window proof and M4's
pre-restart rotation assertions, which would contradict live semantics):
create → authenticates on the next request; delete → AUTH immediately;
grant/revoke → addressing-layer observables while auth keeps succeeding;
grant of a CLI-added config → ``config_topology_restart_required`` + a
restart still needed for access.  ``docker compose restart`` appears only
where it proves restart-harmlessness (a live-applied user/rotation
survives it) or restart necessity (topology).  Rejection observables are
the typed tool-level results of ``_mcp_probe`` (``/mcp`` has no HTTP-401
mapping); every test resets the shared in-memory auth rate-limiter bucket
with a successful probe before expecting a rejection.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
import requests

from tests.integration.conftest_pro import (
    ADMIN_SNAPSHOT_KEY,
    ADMIN_SNAPSHOT_USER,
    PRO_STORE_SECRET,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_PRO_URL = os.environ.get("MCP_PRO_URL", "http://localhost:8080")
_SIMPLE_URL = os.environ.get("MCP_SIMPLE_URL", "http://localhost:8081")

# Must match CALDAV_MCP_ADMIN_KEY on the mcp-pro service in docker-compose.test.yaml.
_ADMIN_KEY = "admin-test-key"

_ADMIN_HEADERS = {"Authorization": f"Bearer {_ADMIN_KEY}"}

# Host-side paths: the bind-mounted store the mcp-pro container writes to.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STORE_PATH = _PROJECT_ROOT / "tests" / "integration" / "pro-store" / "pro-store.db"

# MCP streamable-HTTP endpoint + container used by the live-apply proofs.
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
    """Run the store CLI on the host against the bind-mounted pro store.

    ``CALDAV_MCP_CONFIG_SECRET`` is pinned explicitly (same value the
    ``mcp-pro`` container is started with): commands that store a password
    (``remote add``) must encrypt with that master secret, and a clean host
    environment does not carry it.
    """
    return subprocess.run(
        [sys.executable, "-m", "caldav_mcp.config_cli", "--db", str(_STORE_PATH), *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "CALDAV_MCP_CONFIG_SECRET": PRO_STORE_SECRET},
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


def _mcp_probe(
    username: str,
    key: str,
    tool: str = "caldav_list_calendars",
    arguments: dict | None = None,
) -> tuple[str, str]:
    """Call *tool* as *username*; return ``(status, message)``.

    Default tool is ``caldav_list_calendars`` (parameterless fan-out — the
    auth/visibility probe); pass ``tool``/``arguments`` for dotted-path
    probes.  Observable: the MCP endpoint authenticates at the **tool**
    layer — ``auth.py:_require_auth_db_user`` returns
    ``ToolResult.failure(Status.AUTH, …)`` inside a 200 JSON-RPC response;
    ``/mcp`` has no HTTP-401 mapping (see the "no HTTP 401/403 → auth
    mapping" note in ``test_multi_remote_fanout.py``).  The typed
    ``status == "auth"`` plus the standard unauthorized message is
    therefore the rejection signal.  A rate-limit rejection is also
    ``"auth"`` but carries a different message — assertions pair the status
    with the message so a hot bucket can never pass vacuously.
    """
    with requests.Session() as session:
        _mcp_initialize(session)
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments or {}},
        }
        headers = {
            **_MCP_REQ_HEADERS,
            "X-Mcp-Username": username,
            "Authorization": f"Bearer {key}",
        }
        resp = session.post(_MCP_URL, json=payload, headers=headers)
        resp.raise_for_status()
        rpc = _parse_rpc_response(resp)

    assert "error" not in rpc, f"JSON-RPC-level error: {rpc}"
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


def _mcp_probe_retry(
    username: str,
    key: str,
    tool: str = "caldav_list_calendars",
    arguments: dict | None = None,
    attempts: int = 3,
    delay_seconds: float = 1.0,
) -> tuple[str, str]:
    """Probe with a short retry for transient connection errors (post-restart)."""
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return _mcp_probe(username, key, tool=tool, arguments=arguments)
        except requests.RequestException as exc:
            last_error = exc
            time.sleep(delay_seconds)
    raise AssertionError(f"MCP probe failed after {attempts} attempts: {last_error}")


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
            # M5: live apply — no restart_required, no reload signal on success.
            assert set(body) == {"username", "configs"}
            assert body["username"] == username
            assert body["configs"] == []
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
            assert deleted.json() == {"deleted": True, "username": username}
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
            # "main" is part of the frozen topology: live apply, no signals.
            assert "restart_required" not in grant.json()
            assert "config_topology_restart_required" not in grant.json()

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
            assert "restart_required" not in replaced.json()
            assert "config_topology_restart_required" not in replaced.json()

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
            # M5: live apply — no restart_required, and revoke never carries
            # the config-topology flag (revocation never requires topology).
            assert revoked.json() == {
                "revoked": True,
                "username": username,
                "config": "second",
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
# M5.1 live apply: create/delete take effect on the next MCP request
# ---------------------------------------------------------------------------


class TestAdminLiveApply:
    """User create/delete apply live — these round-trips replace M3's
    restart-window proof (its pre-restart rejection assertion would
    contradict live semantics).

    Rejections are the typed AUTH failure probed by ``_mcp_probe`` (``/mcp``
    has no HTTP-401 mapping — rejection is tool-level, ``auth.py:257-327``).
    Each test runs a successful probe before any rejection so the shared
    in-memory auth rate-limiter bucket (``auth.py`` client-IP quirk, see
    ``test_pro_mode.py:424``) is reset; paired with the "unauthorized"
    message assertion, a rate-limit rejection (also ``"auth"``) can never
    pass vacuously.
    """

    def test_created_user_authenticates_immediately_and_survives_restart(self) -> None:
        """Create → 201 → accepted on the very next request; restart harmless.

        The explicit ``docker compose restart mcp-pro`` + container-health
        poll (no fixed sleeps, fits the 60 s per-test timeout,
        ``Makefile:44``) gates nothing anymore — it only proves the
        live-applied user also survives a snapshot reload: the restart
        step of M3's proof, reduced to its restart-harmless remainder.
        """
        username = "admin-e2e-live-create"
        key = "admin-e2e-live-create-key"
        _delete_user_best_effort(username)  # clear warm-stack leftovers
        try:
            created = requests.post(
                f"{_PRO_URL}/admin/users",
                json={"username": username, "key": key},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert created.status_code == 201, created.text
            # M5: no restart_required, and no reload signal on success.
            assert set(created.json()) == {"username", "configs"}
            assert "admin-e2e-live-create-key" not in created.text

            # Live apply: the fresh user authenticates on the next request.
            # Ungranted user → zero fan-out scopes → status "empty" == auth OK.
            status, message = _mcp_probe_retry(username, key)
            assert status in ("ok", "empty"), (status, message)
            assert "unauthorized" not in message.lower(), message

            # Restart-harmlessness: the live-applied user survives a reload.
            _restart_pro_and_wait_healthy()
            status, message = _mcp_probe_retry(username, key)
            assert status in ("ok", "empty"), (status, message)
            assert "unauthorized" not in message.lower(), message
        finally:
            _delete_user_best_effort(username)

    def test_deleted_user_rejected_immediately(self) -> None:
        """Create → accepted → DELETE → 200 → rejected (AUTH) without restart."""
        username = "admin-e2e-live-delete"
        key = "admin-e2e-live-delete-key"
        _delete_user_best_effort(username)  # clear warm-stack leftovers
        try:
            created = requests.post(
                f"{_PRO_URL}/admin/users",
                json={"username": username, "key": key},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert created.status_code == 201, created.text
            # Successful probe first: resets the shared auth rate-limiter bucket.
            status, message = _mcp_probe_retry(username, key)
            assert status in ("ok", "empty"), (status, message)

            deleted = requests.delete(
                f"{_PRO_URL}/admin/users/{username}",
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert deleted.status_code == 200, deleted.text
            assert deleted.json() == {"deleted": True, "username": username}

            # Auth layer, immediately: the deleted user's next request is rejected.
            status, message = _mcp_probe_retry(username, key)
            assert status == "auth", (status, message)
            assert "unauthorized" in message.lower(), message
        finally:
            _delete_user_best_effort(username)


# ---------------------------------------------------------------------------
# M5.1 key rotation: inversion on the next request (replaces M4's
# restart-inversion test — its pre-restart OLD/NEW window is gone)
# ---------------------------------------------------------------------------


class TestAdminKeyRotation:
    """M5: a key rotated via ``PUT …/key`` applies live — no restart step."""

    def test_rotated_key_inverts_auth_immediately(self) -> None:
        """Rotate → NEW accepted / OLD rejected on the very next request.

        No restart sits between the rotation and the inversion assertions —
        M4's pre-restart window (OLD accepted, NEW rejected until a restart)
        would contradict M5 live semantics.  The trailing
        ``docker compose restart mcp-pro`` + health-wait step (no fixed
        sleeps, fits the 60 s per-test timeout) proves restart-harmlessness
        instead: the live-applied rotation still holds after a snapshot
        reload.

        Rejection is the typed AUTH failure probed by ``_mcp_probe``
        (``/mcp`` has no HTTP-401 mapping), always paired with the
        "unauthorized" message so a rate-limit rejection (also ``"auth"``)
        cannot pass vacuously — and every probe before the first rejection
        is a success, resetting the shared bucket.

        ``admin-snapshot-user`` is rotated but never deleted; the seeded key
        is restored in ``finally``.  Both keys are clearly-fake disposable
        test values and never printed.
        """
        old_key = ADMIN_SNAPSHOT_KEY
        new_key = "admin-snapshot-user-rotated-test-key"
        try:
            # Seeded OLD key authenticates (also resets the shared bucket).
            status, message = _mcp_probe_retry(ADMIN_SNAPSHOT_USER, old_key)
            assert status in ("ok", "empty"), (status, message)
            assert "unauthorized" not in message.lower(), message

            # Rotate via the admin API — response must carry zero key material.
            rotated = requests.put(
                f"{_PRO_URL}/admin/users/{ADMIN_SNAPSHOT_USER}/key",
                json={"key": new_key},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert rotated.status_code == 200, rotated.text
            # M5: live apply — no restart_required, no reload failure signal.
            assert rotated.json() == {"username": ADMIN_SNAPSHOT_USER, "rotated": True}
            assert old_key not in rotated.text
            assert new_key not in rotated.text
            assert "key_hash" not in rotated.text

            # Live inversion — no restart between rotation and these probes.
            status, message = _mcp_probe_retry(ADMIN_SNAPSHOT_USER, new_key)
            assert status in ("ok", "empty"), (status, message)
            assert "unauthorized" not in message.lower(), message
            status, message = _mcp_probe_retry(ADMIN_SNAPSHOT_USER, old_key)
            assert status == "auth", (status, message)
            assert "unauthorized" in message.lower(), message

            # Restart-harmlessness: the rotation survives a snapshot reload.
            _restart_pro_and_wait_healthy()
            status, message = _mcp_probe_retry(ADMIN_SNAPSHOT_USER, new_key)
            assert status in ("ok", "empty"), (status, message)
            assert "unauthorized" not in message.lower(), message
            status, message = _mcp_probe_retry(ADMIN_SNAPSHOT_USER, old_key)
            assert status == "auth", (status, message)
            assert "unauthorized" in message.lower(), message
        finally:
            # Never delete admin-snapshot-user; restore the seeded key so the
            # next run starts from the seeded state even without a store
            # rebuild.  Best-effort: cleanup must never mask the primary
            # failure (mirrors _delete_user_best_effort).
            try:
                requests.put(
                    f"{_PRO_URL}/admin/users/{ADMIN_SNAPSHOT_USER}/key",
                    json={"key": old_key},
                    headers=_ADMIN_HEADERS,
                    timeout=10,
                )
            except requests.RequestException:
                pass


# ---------------------------------------------------------------------------
# M5.1 grant/revoke: addressing-layer observables while auth keeps succeeding
# ---------------------------------------------------------------------------


class TestAdminGrantRevokeLiveApply:
    """grant/revoke move what resolves — never what authenticates.

    Layer split per the M5 deliverable: grants never gate
    ``_require_auth_db_user``, so every probe below authenticates; the
    observables live in the addressing layer (fan-out visibility and the
    generic ``unknown calendar path`` error from ``addressing.py:146-149``).
    """

    def test_revoked_config_stops_resolving_while_auth_succeeds(self) -> None:
        """grant → visible → revoke → invisible + generic path error; auth keeps succeeding."""
        username = "admin-e2e-live-grants"
        key = "admin-e2e-live-grants-key"
        _delete_user_best_effort(username)  # clear warm-stack leftovers
        try:
            created = requests.post(
                f"{_PRO_URL}/admin/users",
                json={"username": username, "key": key},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert created.status_code == 201, created.text

            # Ungranted: auth succeeds, no scopes (bucket reset probe).
            status, message = _mcp_probe_retry(username, key)
            assert status in ("ok", "empty"), (status, message)
            assert "unauthorized" not in message.lower(), message

            granted = requests.post(
                f"{_PRO_URL}/admin/users/{username}/configs",
                json={"config": "main"},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert granted.status_code == 200, granted.text
            assert granted.json()["configs"] == ["main"]
            # "main" is in the frozen topology → no restart/topology signals.
            assert "restart_required" not in granted.json()
            assert "config_topology_restart_required" not in granted.json()

            # Addressing applies live: the config's remotes show up in the fan-out.
            status, message = _mcp_probe_retry(username, key)
            assert status in ("ok", "empty"), (status, message)
            assert "main." in message, message

            revoked = requests.delete(
                f"{_PRO_URL}/admin/users/{username}/configs/main",
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert revoked.status_code == 200, revoked.text
            assert revoked.json() == {
                "revoked": True,
                "username": username,
                "config": "main",
            }

            # Auth still succeeds — revocation never gates _require_auth_db_user …
            status, message = _mcp_probe_retry(username, key)
            assert status != "auth", (status, message)
            assert "unauthorized" not in message.lower(), message
            # … while the calendar vanished from caldav_list_calendars …
            assert "main." not in message, message

            # … and a revoked dotted path fails at the addressing access check
            # (generic unknown-path error, addressing.py:146-149) — no server
            # contact happens, so this failed create has no side effects.
            status, message = _mcp_probe_retry(
                username,
                key,
                tool="caldav_create_event",
                arguments={
                    "calendar_name": "main.radicale.work",
                    "summary": "must never be created",
                    "start": "2026-12-01T10:00:00",
                    "end": "2026-12-01T11:00:00",
                },
            )
            assert status == "error", (status, message)
            assert "unknown calendar path" in message, message
            assert "unauthorized" not in message.lower(), message

            # Re-grant applies live too: the calendar comes back.
            re_granted = requests.post(
                f"{_PRO_URL}/admin/users/{username}/configs",
                json={"config": "main"},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert re_granted.status_code == 200, re_granted.text
            status, message = _mcp_probe_retry(username, key)
            assert status in ("ok", "empty"), (status, message)
            assert "main." in message, message
        finally:
            _delete_user_best_effort(username)


# ---------------------------------------------------------------------------
# M5.1 config-topology freeze: CLI-added config needs a restart for access
# ---------------------------------------------------------------------------


class TestAdminConfigTopologyRestart:
    """Grant of a config added via CLI after startup: flag + restart still needed.

    The grant succeeds at the store (and applies to the user snapshot live),
    but the frozen ``AppConfig`` does not know the config — access keeps
    failing until a restart loads the topology.  The config name is
    unique per run so a warm stack's earlier restart can never have loaded
    it into the frozen singleton.
    """

    def test_cli_added_config_flags_topology_restart_and_access_needs_restart(self) -> None:
        """Grant → config_topology_restart_required; dotted path dead until restart."""
        suffix = uuid4().hex[:8]
        late_config = f"admin-e2e-late-{suffix}"
        username = f"admin-e2e-topo-{suffix}"
        key = f"{username}-key"
        _delete_user_best_effort(username)  # clear warm-stack leftovers
        try:
            # Config + remote + calendar added via CLI *after* startup.
            added = _run_config_cli("config", "add", "--name", late_config)
            assert added.returncode == 0, added.stderr
            added = _run_config_cli(
                "remote",
                "add",
                "--config",
                late_config,
                "--name",
                "relay",
                "--url",
                "http://radicale:5232",
                "--auth-mode",
                "direct",
                "--username",
                "testuser",
                "--password",
                "testpass",
            )
            assert added.returncode == 0, added.stderr
            added = _run_config_cli(
                "calendar", "add", "--config", late_config, "--remote", "relay", "--name", "latecal"
            )
            assert added.returncode == 0, added.stderr

            created = requests.post(
                f"{_PRO_URL}/admin/users",
                json={"username": username, "key": key},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert created.status_code == 201, created.text
            # Bucket-reset probe: auth succeeds (no grants visible yet).
            status, message = _mcp_probe_retry(username, key)
            assert status in ("ok", "empty"), (status, message)

            granted = requests.post(
                f"{_PRO_URL}/admin/users/{username}/configs",
                json={"config": late_config},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert granted.status_code == 200, granted.text
            body = granted.json()
            assert body["configs"] == [late_config]
            # The store write + grant applied, but the topology is frozen:
            assert body["config_topology_restart_required"] is True
            assert "restart_required" not in body

            # Auth keeps succeeding; the frozen fan-out still has no scopes …
            status, message = _mcp_probe_retry(username, key)
            assert status in ("ok", "empty"), (status, message)
            assert "unauthorized" not in message.lower(), message
            assert late_config not in message, message

            # … and the config is not addressable until a restart
            # (generic unknown-path error — no server contact, no side effects).
            status, message = _mcp_probe_retry(
                username,
                key,
                tool="caldav_create_event",
                arguments={
                    "calendar_name": f"{late_config}.relay.latecal",
                    "summary": "must never be created",
                    "start": "2026-12-01T10:00:00",
                    "end": "2026-12-01T11:00:00",
                },
            )
            assert status == "error", (status, message)
            assert "unknown calendar path" in message, message
            assert "unauthorized" not in message.lower(), message

            # Restart loads the CLI-made config from the store → access works.
            _restart_pro_and_wait_healthy()
            status, message = _mcp_probe_retry(username, key)
            assert status in ("ok", "empty"), (status, message)
            assert f"{late_config}.relay" in message, message
        finally:
            _delete_user_best_effort(username)
            # Best-effort cleanup of the CLI-made config (cascades remote +
            # calendar).  A unique name means a leftover never conflicts.
            _run_config_cli("config", "delete", "--name", late_config, "--force")


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

    def test_rotate_key_404_simple_mode(self) -> None:
        """PUT /admin/users/{u}/key on mcp-simple → 404 (M4.1 route absent)."""
        resp = requests.put(
            f"{_SIMPLE_URL}/admin/users/alice/key",
            json={"key": "y"},
            headers=_ADMIN_HEADERS,
            timeout=10,
        )
        assert resp.status_code == 404
