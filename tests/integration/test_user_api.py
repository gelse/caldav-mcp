"""User calendar provisioning integration test (plan D7, full approach).

Requires the compose test stack from ``docker-compose.test.yaml`` (the
``make test-integration`` target builds the pro store and starts it):

* ``mcp-pro`` (http://localhost:8080) — ``DB_CONFIG_ENABLED=true`` → the
  ``POST /user/calendars`` route is registered (no admin key required for
  the route itself); the store mount is read-write, so the provisioning
  write must return 201 — never 503.
* Radicale — the calendar is pre-created server-side with a display name
  equal to the provisioned ``calendar_name``; the route itself never
  contacts the CalDAV server.

Three proof layers in one test: the 201 response contract, the store rows
(personal config + grant + ``u1:``-tagged remote + calendar), and an MCP
dotted-path tool call resolving the returned path against the
live-applied state — per-user decryption, addressing, and the exact
server-side display-name match must all hold, because no restart sits
between the POST and the probe.

Disposables only: a unique ``admin-e2e-*`` user and calendar per run, all
removed in ``finally`` (user via the admin API, store config via the CLI
cascade, collection via caldav) — a crashed run's leftovers never
conflict, matching the admin-API suite's tolerance.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import requests

from caldav_mcp.config_store import ConfigStore
from tests.integration.conftest import RADICALE_PASS, RADICALE_USER
from tests.integration.conftest_pro import PRO_STORE_SECRET

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_PRO_URL = os.environ.get("MCP_PRO_URL", "http://localhost:8080")
_MCP_URL = f"{_PRO_URL}/mcp"

# Must match CALDAV_MCP_ADMIN_KEY on the mcp-pro service in docker-compose.test.yaml.
_ADMIN_KEY = "admin-test-key"
_ADMIN_HEADERS = {"Authorization": f"Bearer {_ADMIN_KEY}"}

# Host-side paths: the bind-mounted store the mcp-pro container writes to.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_STORE_PATH = _PROJECT_ROOT / "tests" / "integration" / "pro-store" / "pro-store.db"

# Compose-DNS view of Radicale — the URL *the server* must reach when it
# dials the provisioned remote (host-side tests use localhost instead).
_RADICALE_CONTAINER_URL = "http://radicale:5232"

_MCP_REQ_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


# ---------------------------------------------------------------------------
# Helpers (module-local copies — integration modules do not cross-import)
# ---------------------------------------------------------------------------


def _delete_user_best_effort(username: str) -> None:
    """Cleanup for ``finally`` blocks — best effort, assertions live in the body."""
    try:
        requests.delete(f"{_PRO_URL}/admin/users/{username}", headers=_ADMIN_HEADERS, timeout=10)
    except requests.RequestException:
        pass


def _delete_store_config_best_effort(config_name: str) -> None:
    """CLI cascade-delete the personal config (remote, calendar, and grant rows)."""
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "caldav_mcp.config_cli",
                "--db",
                str(_STORE_PATH),
                "config",
                "delete",
                "--name",
                config_name,
                "--force",
            ],
            cwd=_PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "CALDAV_MCP_CONFIG_SECRET": PRO_STORE_SECRET},
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


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


def _mcp_call_tool(
    username: str, key: str, tool: str, arguments: dict[str, str]
) -> tuple[str, str]:
    """Call *tool* as *username* on ``/mcp``; return ``(status, message)``.

    Same observables as ``test_admin_api._mcp_probe`` (module-local copy,
    per the no-cross-test-imports convention): the MCP endpoint
    authenticates at the tool layer — a rejection is a typed ``"auth"``
    status inside a 200 JSON-RPC response, never an HTTP 401.
    """
    with requests.Session() as session:
        init_resp = session.post(
            _MCP_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "user-api-integration-test", "version": "0.1.0"},
                },
            },
            headers=_MCP_REQ_HEADERS,
        )
        init_resp.raise_for_status()
        session_id = init_resp.headers.get("mcp-session-id", "")
        if session_id:
            session.headers["mcp-session-id"] = session_id
        session.post(
            _MCP_URL,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=_MCP_REQ_HEADERS,
        )
        rpc_resp = session.post(
            _MCP_URL,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
            headers={
                **_MCP_REQ_HEADERS,
                "X-Mcp-Username": username,
                "Authorization": f"Bearer {key}",
            },
        )
        rpc_resp.raise_for_status()
        rpc = _parse_rpc_response(rpc_resp)

    assert "error" not in rpc, f"JSON-RPC-level error: {rpc}"
    result = rpc.get("result")
    assert isinstance(result, dict), f"Expected result dict, got {rpc!r}"
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return str(structured.get("status", "")), str(structured.get("message", ""))
    content = result.get("content")
    assert isinstance(content, list) and content, f"Empty tool content: {rpc!r}"
    text_payload = json.loads(str(content[0].get("text", "")))
    assert isinstance(text_payload, dict), f"Expected dict text payload, got {rpc!r}"
    return str(text_payload.get("status", "")), str(text_payload.get("message", ""))


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


class TestUserCalendarProvisioning:
    """``POST /user/calendars`` end to end: 201 → store rows → live path."""

    def test_provisions_calendar_and_returned_path_resolves_over_mcp(self, caldav_client) -> None:
        """One call builds config→remote→calendar→grant; the path works immediately."""
        suffix = uuid4().hex[:8]
        username = f"admin-e2e-usercal-{suffix}"
        key = f"{username}-key"
        cal_name = f"usercal-{suffix}"  # charset-safe (no dots), unique per run
        cal = None
        try:
            # Pre-create the collection server-side: the route only registers
            # it, and tools match the server-side display name exactly.
            cal = caldav_client.principal().make_calendar(name=cal_name)

            created = requests.post(
                f"{_PRO_URL}/admin/users",
                json={"username": username, "key": key},
                headers=_ADMIN_HEADERS,
                timeout=10,
            )
            assert created.status_code == 201, created.text

            resp = requests.post(
                f"{_PRO_URL}/user/calendars",
                json={
                    "caldav_url": _RADICALE_CONTAINER_URL,
                    "caldav_username": RADICALE_USER,
                    "caldav_password": RADICALE_PASS,
                    "calendar_name": cal_name,
                },
                headers={
                    "Content-Type": "application/json",
                    "X-Mcp-Username": username,
                    "Authorization": f"Bearer {key}",
                },
                timeout=10,
            )
            # 201 — never 503 (writable mount) — and exactly four keys: no
            # "reload" flag means the live apply completed inside the request.
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert set(body) == {"path", "config", "remote", "calendar"}
            assert body["config"] == f"user-{username}"  # personal-config naming
            assert body["remote"] == cal_name  # defaults to calendar_name
            assert body["calendar"] == cal_name
            assert body["path"] == f"{body['config']}.{cal_name}.{cal_name}"
            # Response hygiene: no credential or store-secret material echoed.
            assert RADICALE_PASS not in resp.text
            assert "u1:" not in resp.text
            assert "enc_secret" not in resp.text

            # Store rows on the host side of the bind mount.
            with ConfigStore(str(_STORE_PATH)) as store:
                assert store.list_config_grants(body["config"]) == (username,)
                snapshot = store.load_config(body["config"])
                remotes = [r for r in snapshot.remotes if r.name == cal_name]
                assert len(remotes) == 1, snapshot.remotes
                remote = remotes[0]
                assert remote.url == _RADICALE_CONTAINER_URL
                assert remote.username == RADICALE_USER
                assert remote.auth_mode == "direct"
                assert remote.password_enc.startswith("u1:")  # per-user encrypted
                calendars = [c.name for c in snapshot.calendars if c.remote_name == cal_name]
                assert calendars == [cal_name]

            # The returned path resolves over MCP with no restart in between:
            # the scope label in the message proves the live-applied state
            # carried config+remote+grant (a dropped or unknown path yields
            # "No accessible calendars" instead of the config.remote line).
            status, message = _mcp_call_tool(
                username,
                key,
                tool="caldav_get_events",
                arguments={"calendar_name": body["path"]},
            )
            assert status in ("ok", "empty"), (status, message)
            assert f"{body['config']}.{body['remote']}" in message, message
            assert "unauthorized" not in message.lower(), message
        finally:
            _delete_user_best_effort(username)
            _delete_store_config_best_effort(f"user-{username}")
            if cal is not None:
                try:
                    cal.delete()
                except Exception:
                    pass  # Best-effort cleanup
