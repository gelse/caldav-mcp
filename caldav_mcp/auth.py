"""Authentication guards and CalDAV credential resolution.

Two-layer authentication model
------------------------------
1. **MCP endpoint auth** — enforced by :func:`_require_auth`.  A shared
   ``CALDAV_MCP_API_KEY`` token is validated against the incoming request's
   ``Authorization: Bearer <token>`` or ``X-Api-Key: <token>`` header.
   Authentication is disabled when the env-var is unset.
2. **CalDAV credentials** — resolved by :func:`_resolve_credentials` for
   each tool invocation via the read-only config singleton
   (:mod:`caldav_mcp.app_config`), which encodes the M1 mode rule.

   - **Env mode**: when ``CALDAV_URL`` is set (non-empty after stripping
     whitespace), all three values (URL, username, password) come from
     environment variables.  ``X-Caldav-*`` headers are ignored entirely.
   - **Header mode**: when ``CALDAV_URL`` is unset or empty, all three
     ``X-Caldav-Url``, ``X-Caldav-Username``, ``X-Caldav-Password`` headers
     are required per request.

   There is no per-field mixing between modes.

Shared runtime state (API key, HTTP header accessors, typed auth errors, and
server constants) is referenced through the :mod:`server` namespace so that
tests which patch ``server.<name>`` observe the same objects used here.

Values that tests may mock (``API_KEY``, ``get_http_headers``) are read
lazily via :func:`_cfg` / :func:`_hdrs` so that ``mock.patch.object(server, …)``
patches are observed at call time.
"""

from caldav_mcp.audit import log_auth_attempt

# Header-name constants are never patched in tests, so direct import is fine.
from caldav_mcp.config import (
    HDR_API_KEY,
    HDR_AUTHORIZATION,
    HDR_PASSWORD,
    HDR_URL,
    HDR_USERNAME,
)
from caldav_mcp.errors import AuthError, Status, ToolResult
from caldav_mcp.rate_limit import auth_rate_limiter


def _cfg():
    """Lazy accessor for ``caldav_mcp.config`` – avoids circular top-level import."""
    from caldav_mcp import config  # noqa: E402  (deferred)

    return config


def _hdrs():
    """Lazy accessor for ``fastmcp.server.dependencies.get_http_headers``."""
    from fastmcp.server.dependencies import get_http_headers  # noqa: E402

    return get_http_headers


def _app():
    """Lazy accessor for :mod:`caldav_mcp.app_config` – avoids circular import."""
    from caldav_mcp import app_config  # noqa: E402  (deferred)

    return app_config


def _get_client_ip() -> str:
    """Extract client IP from request headers or return 'unknown'.

    Checks ``X-Forwarded-For`` and ``X-Real-IP`` headers first (for
    reverse-proxy setups), then falls back to 'unknown'.
    """
    try:
        headers: dict[str, str] = _hdrs()()
        forwarded = headers.get("X-Forwarded-For", "")
        if forwarded:
            # X-Forwarded-For may contain a comma-separated list; take the first.
            return forwarded.split(",")[0].strip()
        real_ip = headers.get("X-Real-IP", "")
        if real_ip:
            return real_ip.strip()
    except Exception:
        pass
    return "unknown"


# NOTE: We use constant-time comparison to prevent timing side-channel
# attacks that could leak the API token byte-by-byte.
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

    Integrates rate limiting (per client IP) and structured audit logging.
    """
    expected = _cfg().API_KEY
    if not expected:
        return None

    client_ip = _get_client_ip()

    # Check rate limit before attempting authentication.
    if auth_rate_limiter.is_rate_limited(client_ip):
        backoff = auth_rate_limiter.get_backoff_seconds(client_ip)
        log_auth_attempt(
            success=False,
            client_ip=client_ip,
            method="none",
            reason=f"rate limited (backoff {backoff}s)",
        )
        return ToolResult.failure(
            Status.AUTH,
            f"rate limited - too many failed attempts, retry in {backoff}s",
        )

    headers = _hdrs()()
    provided = ""
    auth_method = "none"
    auth = headers.get(HDR_AUTHORIZATION, "")
    if auth:
        scheme, _, token = auth.partition(" ")
        if scheme.lower() == "bearer":
            provided = token.strip()
            auth_method = "bearer"
    if not provided:
        key = headers.get(HDR_API_KEY, "").strip()
        if key:
            provided = key
            auth_method = "api-key"

    if provided and _const_eq(provided, expected):
        auth_rate_limiter.reset(client_ip)
        log_auth_attempt(success=True, client_ip=client_ip, method=auth_method)
        return None

    auth_rate_limiter.record_failure(client_ip)
    log_auth_attempt(
        success=False,
        client_ip=client_ip,
        method=auth_method,
        reason="invalid token",
    )
    return ToolResult.failure(Status.AUTH, "unauthorized - missing or invalid API token")


def _resolve_credentials() -> tuple:
    """Return ``(url, username, password)`` using mode-based credential resolution.

    Credentials are resolved from the read-only config singleton
    (:func:`~caldav_mcp.app_config.get_app_config`), which encodes the M1
    mode rule: direct remotes carry env-derived credentials; the passthrough
    remote (header mode) carries per-request header credentials.

    **Env mode** (direct remote) — ``CALDAV_URL`` is set, so the singleton
    carries env-derived URL / username / password.  ``X-Caldav-Url``,
    ``X-Caldav-Username``, ``X-Caldav-Password`` headers are ignored
    entirely.  ``X-Caldav-Username`` and ``X-Caldav-Password`` are reserved
    for a future passthrough mode and are ignored here.

    **Header mode** (passthrough remote) — ``CALDAV_URL`` is unset, so all
    three ``X-Caldav-*`` headers are required per request.

    Raises
    ------
    AuthError
        * In env mode: when the direct remote's username or password is
          empty (``CALDAV_URL`` is set but ``CALDAV_USERNAME`` /
          ``CALDAV_PASSWORD`` are missing).
        * In header mode: when any of the three required headers is missing.
    """
    app = _app().get_app_config()
    remote = _app().implicit_remote(app)

    if remote.auth_mode == "direct":
        # Env mode: headers are ignored entirely (M1 rule). X-Caldav-Username /
        # X-Caldav-Password are reserved for a future passthrough mode.
        username = remote.username
        password = remote.password
        if not username or not password:
            raise AuthError(
                "CALDAV_URL is set but CALDAV_USERNAME/CALDAV_PASSWORD are missing. "
                "Provide all three environment variables, or unset CALDAV_URL and "
                "use the X-Caldav-Url, X-Caldav-Username, X-Caldav-Password headers."
            )
        return remote.url, username, password

    # Passthrough (header mode): all three headers are required per request.
    headers = _hdrs()()
    url = headers.get(HDR_URL, "")
    username = headers.get(HDR_USERNAME, "")
    password = headers.get(HDR_PASSWORD, "")
    if not url or not username or not password:
        raise AuthError(
            "Missing CalDAV credentials. Provide the X-Caldav-Url, "
            "X-Caldav-Username, and X-Caldav-Password headers, or set the "
            "CALDAV_URL, CALDAV_USERNAME, and CALDAV_PASSWORD environment variables."
        )
    return url, username, password
