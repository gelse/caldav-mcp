"""Authentication guards and CalDAV credential resolution.

Two-layer authentication model
------------------------------
1. **MCP endpoint auth** — enforced by :func:`_require_auth`.  Two modes:

   - **Simple mode** (env/header) — a shared ``CALDAV_MCP_API_KEY`` token is
     validated against the incoming request's ``Authorization: Bearer <token>``
     or ``X-Api-Key: <token>`` header.  Authentication is disabled when the
     env-var is unset.
   - **Pro mode** (``"db"``) — DB users sourced from the SQLite store replace
     the env API key.  ``CALDAV_MCP_API_KEY`` is ignored even if set.  The
     username arrives in ``X-Mcp-Username``; the key arrives via the existing
     Bearer / ``X-Api-Key`` transport.  Verification is constant-time against
     the stored PBKDF2 hash.  Per-IP rate limiting and audit logging are
     unchanged.

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
    HDR_MCP_USERNAME,
    HDR_PASSWORD,
    HDR_URL,
    HDR_USERNAME,
)
from caldav_mcp.db_loader import ProUser
from caldav_mcp.errors import AuthError, Status, ToolResult
from caldav_mcp.key_hash import verify_api_key
from caldav_mcp.rate_limit import auth_rate_limiter

# ---------------------------------------------------------------------------
# Pro-user snapshot (populated at startup in db mode — M4.1 / M4.2)
# ---------------------------------------------------------------------------
_pro_users: tuple[ProUser, ...] = ()

_FAILURE_MSG = "unauthorized - missing or invalid credentials"


def configure_pro_users(users: tuple[ProUser, ...]) -> None:
    """Install the pro-user snapshot from the DB loader (called once at startup)."""
    global _pro_users  # noqa: PLW0603
    _pro_users = users


def get_pro_users() -> tuple[ProUser, ...]:
    """Return the installed pro-user snapshot."""
    return _pro_users


def reset_pro_users() -> None:
    """Clear the pro-user snapshot (for testing)."""
    global _pro_users  # noqa: PLW0603
    _pro_users = ()


def _cfg():
    """Lazy accessor for ``caldav_mcp.config`` – avoids circular top-level import."""
    from caldav_mcp import config  # noqa: E402  (deferred)

    return config


def _hdrs():
    """Lazy accessor for ``fastmcp.server.dependencies.get_http_headers``.

    Returns a zero-argument callable that retrieves the current request's HTTP
    headers, including ``authorization`` which FastMCP strips by default.
    """
    from fastmcp.server.dependencies import get_http_headers  # noqa: E402

    def _get(headers: set[str] | None = None) -> dict[str, str]:
        return get_http_headers(include=headers or {"authorization"})

    return _get


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


def _is_pro_mode() -> bool:
    """Return ``True`` when the server is running in pro (DB) mode."""
    app_mod = _app()
    return bool(app_mod.get_app_config().mode == "db")


def _capture_credential_headers() -> dict[str, str]:
    """Capture the current request's ``X-Caldav-*`` headers.

    Returns a copy of the three CalDAV credential headers from the current
    HTTP request context.  Used by the fan-out executor to propagate
    passthrough credentials across sequential remote calls.

    In direct-credential (env) mode the returned dict is empty — passthrough
    scopes that rely on these headers will raise :class:`AuthError` at
    resolution time.
    """
    try:
        headers = _hdrs()()
        return {
            HDR_URL: headers.get(HDR_URL, ""),
            HDR_USERNAME: headers.get(HDR_USERNAME, ""),
            HDR_PASSWORD: headers.get(HDR_PASSWORD, ""),
        }
    except Exception:
        return {}


def _extract_key(headers: dict[str, str]) -> tuple[str, str]:
    """Extract the provided API key from Bearer or X-Api-Key headers.

    Returns ``(provided_key, auth_method)`` where *auth_method* is one of
    ``"bearer"``, ``"api-key"``, or ``"none"``.
    """
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
    return provided, auth_method


def _authenticate() -> "tuple[ProUser | None, ToolResult | None]":
    """Resolve the authenticated user and verify credentials.

    Returns ``(pro_user, None)`` on success — *pro_user* is the
    :class:`~caldav_mcp.db_loader.ProUser` in pro mode, ``None`` in simple
    mode.  Returns ``(None, failure_result)`` on failure.

    This is the single entry-point for all endpoint authentication; callers
    use it to obtain both the auth gate and the resolved pro user (which is
    then forwarded to the addressing layer for access filtering).

    In pro mode the matched :class:`ProUser` is returned on success — this
    is the key difference from :func:`_require_auth` which discards it.

    Internally delegates to :func:`_require_auth` so that tests which patch
    ``caldav_mcp.auth._require_auth`` short-circuit this function.
    """
    err = _require_auth()
    if err is not None:
        return None, err
    # Auth succeeded — locate the matched user for forwarding (pro mode only).
    if _is_pro_mode():
        matched = _find_matched_pro_user()
        return matched, None
    return None, None


def _find_matched_pro_user() -> "ProUser | None":
    """Return the :class:`ProUser` that matches the current request headers.

    This performs the same header scan as :func:`_require_auth_db_user` but
    returns the matched user instead of a ``ToolResult``.  Called only when
    auth has already succeeded — no rate-limit or audit side-effects.
    """
    headers = _hdrs()()
    username = headers.get(HDR_MCP_USERNAME, "").strip()
    for user in get_pro_users():
        if user.username == username:
            return user
    return None  # pragma: no cover (auth already succeeded)


def _require_auth() -> "ToolResult | None":
    """Enforce MCP endpoint authentication.

    In **pro mode** (``mode == "db"``) authentication uses DB users: the
    username arrives in ``X-Mcp-Username`` and the key via Bearer /
    ``X-Api-Key``.  ``CALDAV_MCP_API_KEY`` is ignored.

    In **simple mode** a shared ``CALDAV_MCP_API_KEY`` token is validated.
    Authentication is disabled (returns ``None``) when the env-var is unset.

    Returns ``None`` on success, or a structured auth :class:`ToolResult` to
    return to the client when authentication fails.  Integrates rate limiting
    (per client IP) and structured audit logging.

    This is the auth gate used by :func:`~caldav_mcp.auth._authenticate`
    and by the ``with_caldav_client`` decorator (via module-level lookup).
    Patching ``caldav_mcp.auth._require_auth`` or
    ``caldav_mcp.tools._require_auth`` short-circuits authentication.
    """
    if _is_pro_mode():
        return _require_auth_db_user()
    return _require_auth_simple()


def _require_auth_db_user() -> "ToolResult | None":
    """Pro-mode endpoint auth: username + key verified against DB users."""
    users = get_pro_users()
    if not users:
        return ToolResult.failure(
            Status.AUTH,
            "pro mode enabled but no users configured in the config store",
        )

    client_ip = _get_client_ip()

    # Rate-limit check BEFORE credential work (same semantics as simple mode).
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
    username = headers.get(HDR_MCP_USERNAME, "").strip()
    provided, auth_method = _extract_key(headers)

    # Fail fast: missing/empty key avoids the ~50 ms PBKDF2 work.
    if not provided:
        auth_rate_limiter.record_failure(client_ip)
        log_auth_attempt(
            success=False,
            client_ip=client_ip,
            method="db-user",
            reason="invalid key",
        )
        return ToolResult.failure(Status.AUTH, _FAILURE_MSG)

    # Linear case-sensitive scan for the username.
    matched: ProUser | None = None
    for user in users:
        if user.username == username:
            matched = user
            break

    if matched is None:
        auth_rate_limiter.record_failure(client_ip)
        log_auth_attempt(
            success=False,
            client_ip=client_ip,
            method="db-user",
            reason="unknown user",
        )
        return ToolResult.failure(Status.AUTH, _FAILURE_MSG)

    # Constant-time PBKDF2 verification.
    if not verify_api_key(provided, matched.key_hash):
        auth_rate_limiter.record_failure(client_ip)
        log_auth_attempt(
            success=False,
            client_ip=client_ip,
            method="db-user",
            reason="invalid key",
        )
        return ToolResult.failure(Status.AUTH, _FAILURE_MSG)

    auth_rate_limiter.reset(client_ip)
    log_auth_attempt(success=True, client_ip=client_ip, method="db-user")
    return None


def _require_auth_simple() -> "ToolResult | None":
    """Simple-mode endpoint auth: shared CALDAV_MCP_API_KEY token.

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
    provided, auth_method = _extract_key(headers)

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
    return ToolResult.failure(Status.AUTH, _FAILURE_MSG)


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
