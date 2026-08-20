"""Authentication guards and CalDAV credential resolution.

Configuration constants (API key, HTTP header names) live in
:mod:`caldav_mcp.config`, typed errors and results in :mod:`caldav_mcp.errors`,
and the HTTP header accessor in ``fastmcp.server.dependencies``.  This module
imports directly from those sources, eliminating the previous circular
``import server`` dependency.

Values that tests may mock (``API_KEY``, ``get_http_headers``) are read
lazily via :func:`_cfg` / :func:`_hdrs` so that ``mock.patch.object(server, …)``
patches are observed at call time.
"""

import os

from caldav_mcp.errors import AuthError, Status, ToolResult
# Header-name constants are never patched in tests, so direct import is fine.
from caldav_mcp.config import (
    HDR_API_KEY,
    HDR_AUTHORIZATION,
    HDR_PASSWORD,
    HDR_URL,
    HDR_USERNAME,
)


def _cfg():
    """Lazy accessor for ``caldav_mcp.config`` – avoids circular top-level import."""
    from caldav_mcp import config  # noqa: E402  (deferred)
    return config


def _hdrs():
    """Lazy accessor for ``fastmcp.server.dependencies.get_http_headers``."""
    from fastmcp.server.dependencies import get_http_headers  # noqa: E402
    return get_http_headers


def _const_eq(a: str, b: str) -> bool:
    """Constant-time string comparison to avoid timing attacks on the token."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


def _require_auth() -> "ToolResult | None":
    """Enforce the shared API token, if configured.

    Returns ``None`` on success, or a structured auth :class:`ToolResult` to
    return to the client when authentication fails. Authentication is disabled
    (returns ``None``) when CALDAV_MCP_API_KEY is not set.
    """
    expected = _cfg().API_KEY
    if not expected:
        return None

    headers = _hdrs()()
    provided = ""
    auth = headers.get(HDR_AUTHORIZATION, "")
    if auth:
        scheme, _, token = auth.partition(" ")
        if scheme.lower() == "bearer":
            provided = token.strip()
    if not provided:
        provided = headers.get(HDR_API_KEY, "").strip()

    if provided and _const_eq(provided, expected):
        return None
    return ToolResult.failure(
        Status.AUTH, "unauthorized - missing or invalid API token"
    )


def _resolve_credentials() -> tuple:
    headers = _hdrs()()
    url = headers.get(HDR_URL) or os.environ.get("CALDAV_URL", "")
    username = headers.get(HDR_USERNAME) or os.environ.get("CALDAV_USERNAME", "")
    password = headers.get(HDR_PASSWORD) or os.environ.get("CALDAV_PASSWORD", "")
    if not url or not username or not password:
        raise AuthError(
            "Missing CalDAV credentials. Provide X-Caldav-Url, X-Caldav-Username, "
            "X-Caldav-Password headers, or set CALDAV_URL/CALDAV_USERNAME/"
            "CALDAV_PASSWORD environment variables."
        )
    return url, username, password
