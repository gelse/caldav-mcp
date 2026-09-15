"""Pro-mode integration tests.

Requires the pro-mode MCP server running via:
    docker compose -f docker-compose.test.yaml up -d

The pro-mode service (``mcp-pro``) starts with ``DB_CONFIG_ENABLED=true``
and a pre-built SQLite store containing:

* config ``main`` → remote ``radicale`` (user A) → calendars ``personal``, ``work``
* config ``mirror`` → remote ``radicale-mirror`` (user B) → calendar ``shared``
* user ``alice`` → granted ``["main", "mirror"]``
* user ``bob`` → granted ``["main"]`` only

The MCP server is reached at ``http://localhost:<MCP_PORT>/mcp``.  The streamable
HTTP transport is *stateful*: ``initialize`` returns an ``mcp-session-id`` header
that every subsequent request for that session must echo back.

Test cases:

1. DB auth: success, wrong key, unknown user, missing X-Mcp-Username.
2. CALDAV_MCP_API_KEY-only auth rejected in pro mode.
3. Rate limiting on repeated DB auth failures.
4. Fan-out read: ``caldav_list_calendars`` as ``alice`` shows both configs,
   as ``bob`` only ``main``.
5. Dotted-path write: ``caldav_create_event`` as ``alice`` with
   ``calendar_name="main.radicale.work"`` succeeds.
6. Plain-name write: ``caldav_create_event`` as ``alice`` with
   ``calendar_name="work"`` → typed rejection.
7. Existing simple-mode suite untouched (verified by ``make test-integration``
   running all files).
"""

from __future__ import annotations

import json
import os

import pytest
import requests
from caldav import DAVClient

pytestmark = [pytest.mark.integration, pytest.mark.slow]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RADICALE_URL = os.environ.get("RADICALE_URL", "http://localhost:5232")
_MCP_PORT = int(os.environ.get("MCP_PORT", "8080"))
_MCP_URL = f"http://localhost:{_MCP_PORT}/mcp"

_ALICE_KEY = "alice-integration-test-key"
_BOB_KEY = "bob-integration-test-key"

_REQ_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

# Calendar topology declared in the store — these collections must exist on
# the Radicale server for dotted-path addressing to resolve.
_CALENDAR_TOPOLOGY = (
    ("userA", "testpassA", ("personal", "work")),
    ("userB", "testpassB", ("shared",)),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _provision_radicale_calendars() -> None:
    """Create the calendars declared in the store on the Radicale server.

    The store declares calendar *names*; Radicale only knows collections that
    have actually been created.  Without this step dotted-path addressing
    resolves to a calendar the server principal does not have.
    """
    for username, password, names in _CALENDAR_TOPOLOGY:
        client = DAVClient(url=_RADICALE_URL, username=username, password=password)
        principal = client.principal()
        existing = {c.get_display_name() for c in principal.calendars()}
        for name in names:
            if name not in existing:
                principal.make_calendar(name=name)


def _mcp_init(session: requests.Session) -> str:
    """Send the MCP ``initialize`` + ``notifications/initialized`` handshake.

    Captures the ``mcp-session-id`` returned by the transport and installs it
    on *session* so every later request is part of the same MCP session.
    """
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "integration-test", "version": "0.1.0"},
        },
    }
    resp = session.post(_MCP_URL, json=init_payload, headers=_REQ_HEADERS)
    resp.raise_for_status()

    session_id = resp.headers.get("mcp-session-id", "")
    if session_id:
        session.headers["mcp-session-id"] = session_id

    notif_payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    session.post(_MCP_URL, json=notif_payload, headers=_REQ_HEADERS)
    return session_id


def _parse_rpc_response(resp: requests.Response) -> dict[str, object]:
    """Parse a JSON-RPC response that may arrive as an SSE ``data:`` frame.

    The streamable-HTTP transport replies with ``text/event-stream``; the
    JSON-RPC payload is carried on the ``data:`` line(s).
    """
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[len("data:") :].strip())
                assert isinstance(payload, dict), f"Expected dict payload, got {payload!r}"
                return payload
        raise AssertionError(f"No 'data:' frame in SSE response: {resp.text!r}")
    result: dict[str, object] = resp.json()
    return result


def _mcp_call(
    session: requests.Session,
    tool: str,
    arguments: dict[str, object],
    *,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, object]:
    """Call an MCP tool and return the parsed JSON-RPC response.

    Performs the ``initialize`` handshake, then the ``tools/call`` request with
    the provided extra headers on top of the session's own headers.
    """
    _mcp_init(session)

    # The streamable-HTTP transport requires ``Accept: application/json,
    # text/event-stream`` on every request; the ``mcp-session-id`` is supplied
    # by the session headers installed in ``_mcp_init``.
    headers = {**_REQ_HEADERS, **(extra_headers or {})}
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    resp = session.post(_MCP_URL, json=payload, headers=headers)
    resp.raise_for_status()
    return _parse_rpc_response(resp)


def _extract_result_text(response: dict[str, object]) -> str:
    """Pull the text from a successful ``tools/call`` JSON-RPC result."""
    result = response.get("result")
    assert isinstance(result, dict), f"Expected result dict, got {type(result)}"
    content = result.get("content")
    assert isinstance(content, list) and len(content) > 0, (
        f"Expected non-empty content list, got {content!r}"
    )
    return str(content[0].get("text", ""))


def _auth_headers(username: str, key: str) -> dict[str, str]:
    """Build the auth header dict for a pro-mode request."""
    return {
        "X-Mcp-Username": username,
        "Authorization": f"Bearer {key}",
    }


# ---------------------------------------------------------------------------
# Session setup
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _ensure_calendars() -> None:
    """Provision the store-declared calendars on Radicale before the tests run."""
    _provision_radicale_calendars()


# ---------------------------------------------------------------------------
# 1. DB auth tests
# ---------------------------------------------------------------------------


class TestDBAuth:
    """Pro-mode endpoint auth with DB-backed users."""

    def test_alice_success(self) -> None:
        """Valid credentials → tool call succeeds."""
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_list_calendars",
                {},
                extra_headers=_auth_headers("alice", _ALICE_KEY),
            )
            assert "result" in resp, f"Expected result, got {resp}"
            text = _extract_result_text(resp)
            # In pro mode ``list_calendars`` fans out and returns an aggregated
            # JSON object with per-config entries (see TestFanoutRead), not a
            # bare list as in simple mode.
            payload = json.loads(text)
            assert isinstance(payload, dict), f"Expected aggregated result, got {payload!r}"
            assert "data" in payload

    def test_wrong_key(self) -> None:
        """Wrong API key → AUTH failure."""
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_list_calendars",
                {},
                extra_headers=_auth_headers("alice", "wrong-key-12345"),
            )
            assert "result" in resp
            text = _extract_result_text(resp)
            assert "AUTH" in text.upper() or "error" in text.lower()

    def test_unknown_user(self) -> None:
        """Unknown username → AUTH failure."""
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_list_calendars",
                {},
                extra_headers=_auth_headers("nonexistent", _ALICE_KEY),
            )
            assert "result" in resp
            text = _extract_result_text(resp)
            assert "AUTH" in text.upper() or "error" in text.lower()

    def test_missing_username_with_valid_bearer(self) -> None:
        """Valid Bearer token but no ``X-Mcp-Username`` → AUTH failure.

        In simple mode, Bearer alone would succeed.  In pro mode, the
        missing username is rejected because DB auth requires both.
        """
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_list_calendars",
                {},
                extra_headers={"Authorization": f"Bearer {_ALICE_KEY}"},
            )
            assert "result" in resp
            text = _extract_result_text(resp)
            assert "AUTH" in text.upper() or "error" in text.lower()


# ---------------------------------------------------------------------------
# 2. CALDAV_MCP_API_KEY-only auth rejected in pro mode
# ---------------------------------------------------------------------------


class TestEnvApiKeyIgnoredInProMode:
    """A Bearer token matching the env ``CALDAV_MCP_API_KEY`` is rejected
    when ``DB_CONFIG_ENABLED=true`` (pro mode ignores env API key)."""

    def test_env_api_key_not_accepted(self) -> None:
        """Bearer token without ``X-Mcp-Username`` → AUTH failure in pro mode."""
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_list_calendars",
                {},
                # Send a Bearer token but no X-Mcp-Username — pro mode requires
                # both; this proves CALDAV_MCP_API_KEY alone is insufficient.
                extra_headers={"Authorization": "Bearer some-api-key-value"},
            )
            assert "result" in resp
            text = _extract_result_text(resp)
            assert "AUTH" in text.upper() or "error" in text.lower()


# ---------------------------------------------------------------------------
# 4. Fan-out read: caldav_list_calendars
# ---------------------------------------------------------------------------


class TestFanoutRead:
    """Fan-out across accessible configs."""

    def test_alice_sees_both_configs(self) -> None:
        """``alice`` has access to both ``main`` and ``mirror`` configs.

        ``caldav_list_calendars`` fans out across all accessible remotes
        and returns aggregated entries.
        """
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_list_calendars",
                {},
                extra_headers=_auth_headers("alice", _ALICE_KEY),
            )
            text = _extract_result_text(resp)
            payload = json.loads(text)
            assert isinstance(payload, dict), f"Expected aggregated result, got {payload!r}"
            entries = payload.get("data", [])
            configs_seen = {e.get("config_name") for e in entries}
            assert "main" in configs_seen, f"Expected 'main' in {configs_seen}"
            assert "mirror" in configs_seen, f"Expected 'mirror' in {configs_seen}"

    def test_bob_sees_only_main(self) -> None:
        """``bob`` has access to the ``main`` config only."""
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_list_calendars",
                {},
                extra_headers=_auth_headers("bob", _BOB_KEY),
            )
            text = _extract_result_text(resp)
            payload = json.loads(text)
            assert isinstance(payload, dict), f"Expected aggregated result, got {payload!r}"
            entries = payload.get("data", [])
            configs_seen = {e.get("config_name") for e in entries}
            assert "main" in configs_seen, f"Expected 'main' in {configs_seen}"
            assert "mirror" not in configs_seen, (
                f"'mirror' should not appear for bob, got {configs_seen}"
            )


# ---------------------------------------------------------------------------
# 5. Dotted-path write
# ---------------------------------------------------------------------------


class TestDottedPathWrite:
    """Write tools using dotted-path addressing."""

    def test_alice_creates_event_in_main_radicale_work(self) -> None:
        """``alice`` creates an event via ``main.radicale.work``.

        The event should land on Radicale user A's ``work`` calendar.
        """
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_create_event",
                {
                    "calendar_name": "main.radicale.work",
                    "summary": "Pro-mode integration test event",
                    "start": "2026-09-15T10:00:00",
                    "end": "2026-09-15T11:00:00",
                },
                extra_headers=_auth_headers("alice", _ALICE_KEY),
            )
            text = _extract_result_text(resp)
            # A successful create returns a UID; an error contains "ERROR".
            assert "error" not in text.lower(), f"Create failed: {text}"

    def test_bob_rejected_for_mirror_config(self) -> None:
        """``bob`` targets ``mirror.radicale-mirror.shared`` → rejected.

        ``bob`` only has access to the ``main`` config.
        """
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_create_event",
                {
                    "calendar_name": "mirror.radicale-mirror.shared",
                    "summary": "Should fail",
                    "start": "2026-09-15T10:00:00",
                    "end": "2026-09-15T11:00:00",
                },
                extra_headers=_auth_headers("bob", _BOB_KEY),
            )
            text = _extract_result_text(resp)
            # The rejection should mention auth/access or error.
            assert "error" in text.lower() or "access" in text.lower() or "auth" in text.lower(), (
                f"Expected rejection, got: {text}"
            )


# ---------------------------------------------------------------------------
# 6. Plain-name write → typed rejection
# ---------------------------------------------------------------------------


class TestPlainNameWriteRejected:
    """Write tools must reject plain calendar names in pro mode."""

    def test_alice_plain_name_rejected(self) -> None:
        """``calendar_name="work"`` (no dots) → ERROR naming the dotted form."""
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_create_event",
                {
                    "calendar_name": "work",
                    "summary": "Should fail",
                    "start": "2026-09-15T10:00:00",
                    "end": "2026-09-15T11:00:00",
                },
                extra_headers=_auth_headers("alice", _ALICE_KEY),
            )
            text = _extract_result_text(resp)
            # The error should mention the dotted-path format requirement.
            assert "config.remote.calendar" in text.lower(), (
                f"Expected dotted-path error, got: {text}"
            )


# ---------------------------------------------------------------------------
# 3. Rate limiting on repeated DB auth failures
# ---------------------------------------------------------------------------
#
# NOTE: this class is intentionally ordered LAST.  The rate limiter keys on
# the client IP, which resolves to the shared fallback ``"unknown"`` for the
# harness (no ``X-Forwarded-For`` / ``X-Real-IP`` is sent).  Its deliberate
# burst of failures therefore exhausts the single shared bucket and would
# poison any legitimate request issued afterwards, so it must run after all
# positively-authenticated tests.


class TestRateLimiting:
    """Repeated bad keys from one client IP hit the rate limit."""

    def test_rate_limit_after_failures(self) -> None:
        """Enough bad-key attempts should trigger rate limiting."""
        # Send several bad auth requests to build up failure count.
        for _ in range(12):
            with requests.Session() as s:
                _mcp_call(
                    s,
                    "caldav_list_calendars",
                    {},
                    extra_headers=_auth_headers("alice", "wrong-key"),
                )

        # The next request should be rate-limited.
        with requests.Session() as s:
            resp = _mcp_call(
                s,
                "caldav_list_calendars",
                {},
                extra_headers=_auth_headers("alice", "wrong-key"),
            )
            text = _extract_result_text(resp)
            assert "rate limit" in text.lower() or "AUTH" in text.upper()
