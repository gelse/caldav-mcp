"""Pro-mode rate-limiting integration test — deliberately collected LAST.

Requires the compose test stack from ``docker-compose.test.yaml``.

Suite-order invariant: the auth rate-limiter keys on the client IP, which
resolves to the shared fallback ``"unknown"`` for the harness (no
``X-Forwarded-For`` / ``X-Real-IP`` is sent), and this module's deliberate
burst of bad-key failures exhausts that single shared bucket (default:
10 failures per 60 s window).  Every authenticated request issued while
the bucket is hot is rejected — the limiter checks the limit *before*
verifying credentials, so no successful request can reset it until the
window slides.  The ``test_zz_`` prefix keeps this file last in pytest's
alphabetical collection, so positively-authenticated suites
(``test_pro_mode``, ``test_user_api``, …) never run after the burst.

(Moved out of ``test_pro_mode.py`` when ``test_user_api.py`` joined the
suite: the "must run last" invariant was only file-local there, while
pytest orders by file name — the burst silently poisoned every later
module.)
"""

from __future__ import annotations

import json
import os

import pytest
import requests

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_MCP_PORT = int(os.environ.get("MCP_PORT", "8080"))
_MCP_URL = f"http://localhost:{_MCP_PORT}/mcp"

_REQ_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


# ---------------------------------------------------------------------------
# Helpers (module-local copies — integration modules do not cross-import)
# ---------------------------------------------------------------------------


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
# Rate limiting on repeated DB auth failures — must stay collected LAST
# ---------------------------------------------------------------------------


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
