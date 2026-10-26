"""Admin REST API for pro-mode user management (M2: read endpoints).

Plain REST routes on the existing FastMCP/Starlette app — never MCP tools
(fixed constraint, Design (a)).  ``server.py`` registers them in the pro
branch only, when ``DB_CONFIG_ENABLED`` is true **and**
``CALDAV_MCP_ADMIN_KEY`` is non-empty, before ``run_http_async``; everywhere
else the routes do not exist and requests receive the framework's 404.

Guard order (Design (b), milestone M2.3):

1. ``_is_pro_mode()`` → 404 (defense in depth: routes are never registered
   outside pro mode, so a registration bug still cannot expose them).
2. ``ADMIN_KEY`` non-empty → 404 fail-closed **before** any comparison
   (``_const_eq("", "")`` is ``True``).
3. Client IP — ``X-Forwarded-For`` first hop, else ``request.client.host``.
   Deliberately not ``auth._get_client_ip``: its mixed-case header lookups
   miss fastmcp's lowercase header dict and would collapse every caller
   into one ``"unknown"`` rate-limit bucket (known repo quirk).
4. ``admin_rate_limiter`` → 429 + ``Retry-After`` when the IP is limited.
5. ``_extract_key`` + constant-time ``_const_eq`` → 401 + audit +
   ``record_failure`` on mismatch; ``reset`` + success audit on match.

Handlers open a fresh :class:`~caldav_mcp.config_store.ConfigStore` per
request (Design (c); no long-lived connection, no ``check_same_thread``
exposure) and funnel store errors through the single choke point
:func:`_map_store_error` (Design (d)).  User payloads are always
``{"username", "configs"}`` — never ``key_hash``, never ``key``.
"""

from __future__ import annotations

import os
import sqlite3

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from caldav_mcp import config
from caldav_mcp.audit import log_admin_operation, log_auth_attempt
from caldav_mcp.auth import _FAILURE_MSG, _const_eq, _extract_key, _hdrs, _is_pro_mode
from caldav_mcp.config_store import (
    ConfigStore,
    StoreConflictError,
    StoreError,
    StoreNotFoundError,
    StoreSchemaError,
    StoreValidationError,
    UserRecord,
)
from caldav_mcp.errors import log
from caldav_mcp.rate_limit import admin_rate_limiter

# API-layer username transport guard limits (Design (d)) — not a store
# charset rule; the store accepts usernames the API cannot address.
_MAX_USERNAME_LENGTH = 128


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    """Best-effort client IP for rate-limit bucketing and audit (Design (b)).

    ``X-Forwarded-For`` first hop wins when present and non-empty (reverse
    proxy setups); an empty/whitespace-only hop falls back to the socket
    peer so degenerate headers cannot collapse callers into one bucket.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        hop = forwarded.split(",")[0].strip()
        if hop:
            return hop
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


def _require_admin(request: Request) -> JSONResponse | None:
    """Authenticate the request as an admin API call (Design (b)).

    Returns ``None`` when the caller may proceed, otherwise the JSON error
    response to return (404 disabled / fail-closed, 429 rate-limited,
    401 missing or invalid key).
    """
    if not _is_pro_mode():
        return JSONResponse({"error": "not found"}, status_code=404)
    admin_key = config.ADMIN_KEY
    if not admin_key:
        # Fail-closed BEFORE any comparison: _const_eq("", "") is True.
        return JSONResponse({"error": "not found"}, status_code=404)

    client_ip = _client_ip(request)
    if admin_rate_limiter.is_rate_limited(client_ip):
        backoff = admin_rate_limiter.get_backoff_seconds(client_ip)
        return JSONResponse(
            {"error": f"rate limited - too many failed attempts, retry in {backoff}s"},
            status_code=429,
            headers={"Retry-After": str(backoff)},
        )

    provided, _auth_method = _extract_key(_hdrs()())
    if provided and _const_eq(provided, admin_key):
        admin_rate_limiter.reset(client_ip)
        log_auth_attempt(success=True, client_ip=client_ip, method="admin-api")
        return None

    admin_rate_limiter.record_failure(client_ip)
    log_auth_attempt(
        success=False,
        client_ip=client_ip,
        method="admin-api",
        reason="invalid key",
    )
    return JSONResponse({"error": _FAILURE_MSG}, status_code=401)


def _username_transport_error(username: str) -> str | None:
    """HTTP-transport validation for a path-decoded username (Design (d)).

    Store usernames are free-form (non-empty only); this API-layer guard
    rejects values that cannot be safely addressed in a URL path.  It is
    transport validation, never a duplication of store charset rules.
    """
    if len(username) > _MAX_USERNAME_LENGTH:
        return f"username must not exceed {_MAX_USERNAME_LENGTH} characters"
    if "/" in username:
        return "username must not contain '/'"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in username):
        return "username must not contain control characters"
    return None


def _db_path() -> str:
    """Resolve the store path per request (lazy; validated at pro-mode startup)."""
    path = os.environ.get("CALDAV_MCP_DB_PATH", "").strip()
    if not path:
        raise StoreError("CALDAV_MCP_DB_PATH is not set")
    return path


def _map_store_error(exc: Exception) -> JSONResponse:
    """Single choke point: store/OS exception → HTTP error body (Design (d))."""
    if isinstance(exc, StoreValidationError):
        status_code, message = 400, str(exc)
    elif isinstance(exc, StoreNotFoundError):
        status_code, message = 404, str(exc)
    elif isinstance(exc, StoreConflictError):
        status_code, message = 409, str(exc)
    elif isinstance(exc, StoreSchemaError):
        status_code, message = 503, f"config store unavailable: {exc}"
    elif isinstance(exc, sqlite3.OperationalError):
        # Locked or unreadable store — carries the retry hint (edge-case table).
        status_code, message = 503, f"config store unavailable ({exc}) - retry shortly"
    elif isinstance(exc, StoreError):
        status_code, message = 503, f"config store unavailable: {exc}"
    else:
        # 500: generic message; the detail goes to the log only (status policy).
        log.error("admin API unexpected error: %s: %s", type(exc).__name__, exc, exc_info=True)
        status_code, message = 500, "internal server error"
    return JSONResponse({"error": message}, status_code=status_code)


def _user_payload(record: UserRecord) -> dict[str, object]:
    """API user object: ``username`` + ``configs`` only — never key material."""
    return {"username": record.username, "configs": list(record.config_names)}


# ---------------------------------------------------------------------------
# GET handlers (M2 — read-only)
# ---------------------------------------------------------------------------


async def _handle_list_users(request: Request) -> JSONResponse:
    """``GET /admin/users`` — list store users in insertion order."""
    denied = _require_admin(request)
    if denied is not None:
        return denied
    client_ip = _client_ip(request)
    try:
        with ConfigStore(_db_path()) as store:
            users = store.list_users()
    except Exception as exc:
        log_admin_operation(action="list_users", status="error", client_ip=client_ip)
        return _map_store_error(exc)
    log_admin_operation(action="list_users", status="ok", client_ip=client_ip)
    return JSONResponse({"users": [_user_payload(u) for u in users]})


async def _handle_get_user(request: Request) -> JSONResponse:
    """``GET /admin/users/{username}`` — one store user."""
    denied = _require_admin(request)
    if denied is not None:
        return denied
    username = request.path_params.get("username", "")
    transport_error = _username_transport_error(username)
    if transport_error is not None:
        return JSONResponse({"error": transport_error}, status_code=400)
    client_ip = _client_ip(request)
    try:
        with ConfigStore(_db_path()) as store:
            record = store.get_user(username)
    except Exception as exc:
        log_admin_operation(
            action="get_user", status="error", target_user=username, client_ip=client_ip
        )
        return _map_store_error(exc)
    log_admin_operation(action="get_user", status="ok", target_user=username, client_ip=client_ip)
    return JSONResponse(_user_payload(record))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def configure_admin_routes(mcp: FastMCP) -> None:
    """Register the admin REST routes on *mcp* (called from ``server.py``).

    Only the two M2 read routes are registered; the write routes of M3/M4
    do not exist yet, so requests to them yield framework 404/405.  Must be
    called before ``run_http_async`` — fastmcp merges additional routes into
    the streamable-http app with lowest precedence (M2.1 spike).
    """
    mcp.custom_route("/admin/users", methods=["GET"])(_handle_list_users)
    mcp.custom_route("/admin/users/{username}", methods=["GET"])(_handle_get_user)
