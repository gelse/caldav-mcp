"""Integration tests for the MCP endpoint's shared API-token auth (issue #18).

Reproduces https://github.com/gelse/caldav-mcp/issues/18 —
``Authorization: Bearer <key>`` is rejected by the endpoint auth even when the
key is correct, while ``X-Api-Key: <key>`` with the same key is accepted.

Unlike the rest of this suite, these tests do not need Radicale: they launch
the real server (``server.py``) as a subprocess with a known
``CALDAV_MCP_API_KEY`` and talk to it over real HTTP, so the auth layer sees
genuine request headers (no headers are mocked anywhere).

No CalDAV credentials are given to the server on purpose: a request that gets
*past* endpoint auth proceeds to credential resolution and fails with the
distinct ``Missing CalDAV credentials`` message.  That cleanly separates
endpoint-auth acceptance (X-Api-Key) from endpoint-auth rejection
(``unauthorized - missing or invalid API token``) without involving a CalDAV
server at all.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_SCRIPT = REPO_ROOT / "server.py"

# 32-character key, mirroring the key length shown in issue #18.
TEST_API_KEY = "issue18-bearer-auth-test-key1234"

# Exact message returned by caldav_mcp.auth._require_auth() when the endpoint
# auth rejects a request (errors.py renders Status.AUTH as "ERROR:[auth] ...").
AUTH_FAILURE_MESSAGE = "unauthorized - missing or invalid API token"

# Message produced by caldav_mcp.auth._resolve_credentials() AFTER endpoint
# auth passed — only reachable once _require_auth() returned None.
MISSING_CREDS_MESSAGE = "Missing CalDAV credentials"


def _free_port() -> int:
    """Pick a currently-free localhost TCP port for the server subprocess."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _httpx_client_factory(**kwargs):
    """Build the client fastmcp's HTTP transport uses (proxy-free, deterministic)."""
    kwargs["trust_env"] = False
    return httpx.AsyncClient(**kwargs)


def _wait_until_ready(proc: subprocess.Popen, url: str, timeout: float) -> None:
    """Poll the endpoint until it answers any HTTP request (or fail loudly)."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server process exited early with code {proc.returncode}")
        try:
            # Any HTTP response — even 405/406 — means the endpoint is listening.
            httpx.get(url, timeout=2.0, trust_env=False)
            return
        except httpx.HTTPError as exc:
            last_error = exc
            time.sleep(0.2)
    raise TimeoutError(
        f"server did not start listening within {timeout}s (last error: {last_error})"
    )


def _result_text(result) -> str:
    """Flatten everything a tool call returned into one searchable string."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        parts.append(json.dumps(structured, default=str))
    data = getattr(result, "data", None)
    if data is not None:
        parts.append(str(data))
    parts.append(str(result))
    return "\n".join(parts)


def _call_list_calendars(server_url: str, headers: dict[str, str] | None):
    """Call ``caldav_list_calendars`` through a real Streamable HTTP session.

    ``raise_on_error=False`` so auth/credential failures come back as content
    to assert on instead of raising.
    """

    async def _run():
        transport = StreamableHttpTransport(
            url=server_url,
            headers=headers or {},
            httpx_client_factory=_httpx_client_factory,
        )
        async with Client(transport) as client:
            return await client.call_tool("caldav_list_calendars", {}, raise_on_error=False)

    return asyncio.run(_run())


@pytest.fixture(scope="module")
def mcp_server():
    """Boot ``server.py`` as a subprocess with a known API key; yield its endpoint URL.

    Mirrors the issue #18 setup: ``CALDAV_MCP_API_KEY`` comes from the
    environment, the key itself arrives per-request via HTTP headers.  CalDAV
    credentials and TLS settings are stripped so endpoint-auth behaviour is the
    only variable under test.
    """
    port = _free_port()
    env = os.environ.copy()
    env["CALDAV_MCP_API_KEY"] = TEST_API_KEY
    env["CALDAV_MCP_PORT"] = str(port)
    env["CALDAV_MCP_PATH"] = "/mcp"
    # Generous limit so the in-process rate limiter can never mask the auth
    # result with a "rate limited" failure while several tests share the server.
    env["CALDAV_MCP_RATE_LIMIT_MAX_FAILURES"] = "1000"
    env["PYTHONUNBUFFERED"] = "1"
    env["TZ"] = "UTC"
    for name in (
        "CALDAV_URL",
        "CALDAV_USERNAME",
        "CALDAV_PASSWORD",
        "CALDAV_MCP_TLS_CERT",
        "CALDAV_MCP_TLS_KEY",
        "CALDAV_MCP_TLS_CA_BUNDLE",
    ):
        env.pop(name, None)

    proc = subprocess.Popen(
        [sys.executable, str(SERVER_SCRIPT)],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        _wait_until_ready(proc, url, timeout=30.0)
    except Exception:
        try:
            proc.kill()
            output, _ = proc.communicate(timeout=10)
        except Exception:
            output = b""
        raise RuntimeError(
            "MCP server failed to start.\n--- server output ---\n" + output.decode(errors="replace")
        ) from None

    yield SimpleNamespace(url=url, proc=proc)

    proc.terminate()
    try:
        proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate(timeout=10)


class TestEndpointAuthHeaders:
    """Bearer vs X-Api-Key behaviour of the shared-token endpoint auth (issue #18)."""

    def test_auth_is_enforced_without_any_credentials(self, mcp_server):
        """Baseline: an unauthenticated request is rejected with the auth-layer message.

        This guards the constants used by the tests below: if it fails, the
        endpoint auth did not engage at all and any other result would be
        meaningless.
        """
        result = _call_list_calendars(mcp_server.url, headers={})
        text = _result_text(result)
        assert AUTH_FAILURE_MESSAGE in text, (
            "expected the endpoint auth to reject an unauthenticated request with "
            f"{AUTH_FAILURE_MESSAGE!r}; got: {text!r}"
        )

    def test_bearer_token_with_correct_key_is_authorized(self, mcp_server):
        """PRIMARY reproduction for issue #18: a correct Bearer token must be accepted.

        This test is EXPECTED TO FAIL while the bug is present: the server
        never sees the Authorization header and rejects the request with
        ``unauthorized - missing or invalid API token``.

        Symmetric with the X-Api-Key contrast case below, acceptance is proven
        positively: passing endpoint auth means the call advanced to credential
        resolution and stops with the distinct ``Missing CalDAV credentials``
        message.
        """
        result = _call_list_calendars(
            mcp_server.url,
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )
        text = _result_text(result)
        assert AUTH_FAILURE_MESSAGE not in text, (
            "issue #18 reproduced: the correct `Authorization: Bearer <key>` header was "
            f"rejected by the endpoint auth. Response: {text!r}"
        )
        assert MISSING_CREDS_MESSAGE in text, (
            "expected the request to pass endpoint auth and stop at CalDAV credential "
            f"resolution with {MISSING_CREDS_MESSAGE!r}; got: {text!r}"
        )

    def test_x_api_key_with_correct_key_is_authorized(self, mcp_server):
        """Contrast: the same key via X-Api-Key must pass endpoint auth.

        Passing endpoint auth means ``_require_auth()`` returned None and the
        call advanced to credential resolution, which stops here with the
        distinct ``Missing CalDAV credentials`` message (no CalDAV credentials
        are configured for the server).
        """
        result = _call_list_calendars(
            mcp_server.url,
            headers={"X-Api-Key": TEST_API_KEY},
        )
        text = _result_text(result)
        assert AUTH_FAILURE_MESSAGE not in text, (
            "X-Api-Key with the correct key was rejected by the endpoint auth; per "
            f"issue #18 only the Bearer path is expected to fail. Response: {text!r}"
        )
        assert MISSING_CREDS_MESSAGE in text, (
            "expected the request to pass endpoint auth and stop at CalDAV credential "
            f"resolution with {MISSING_CREDS_MESSAGE!r}; got: {text!r}"
        )
