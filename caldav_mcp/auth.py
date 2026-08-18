"""Authentication guards and CalDAV credential resolution.

Shared runtime state (API key, HTTP header accessors, typed auth errors, and
server constants) is referenced through the :mod:`server` namespace so that
tests which patch ``server.<name>`` observe the same objects used here.
"""

import os

import server


def _const_eq(a: str, b: str) -> bool:
    """Constant-time string comparison to avoid timing attacks on the token."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


def _require_auth() -> str:
    """Enforce the shared API token, if configured.

    Returns an empty string on success, or an auth error string to return to the
    client when authentication fails. Authentication is disabled (returns "") when
    CALDAV_MCP_API_KEY is not set.
    """
    expected = server.API_KEY
    if not expected:
        return ""

    headers = server.get_http_headers()
    provided = ""
    auth = headers.get(server.HDR_AUTHORIZATION, "")
    if auth:
        scheme, _, token = auth.partition(" ")
        if scheme.lower() == "bearer":
            provided = token.strip()
    if not provided:
        provided = headers.get(server.HDR_API_KEY, "").strip()

    if provided and _const_eq(provided, expected):
        return ""
    return "ERROR: unauthorized - missing or invalid API token"


def _resolve_credentials() -> tuple:
    headers = server.get_http_headers()
    url = headers.get(server.HDR_URL) or os.environ.get("CALDAV_URL", "")
    username = headers.get(server.HDR_USERNAME) or os.environ.get("CALDAV_USERNAME", "")
    password = headers.get(server.HDR_PASSWORD) or os.environ.get("CALDAV_PASSWORD", "")
    if not url or not username or not password:
        raise server.AuthError(
            "Missing CalDAV credentials. Provide X-Caldav-Url, X-Caldav-Username, "
            "X-Caldav-Password headers, or set CALDAV_URL/CALDAV_USERNAME/"
            "CALDAV_PASSWORD environment variables."
        )
    return url, username, password
