"""Admin REST API for pro-mode user management (M2: read, M3: write, M4: rotation, M5: live reload).

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

M3 write routes follow the milestone flow: guard → parse/validate body →
fresh store (with the ``get_user``/``list_configs`` pre-checks mandated by
table (d)) → store call → ``log_admin_operation`` → JSON response.  The M4
rotation route ``PUT /admin/users/{username}/key`` follows Design (f):
guard → reject empty key 400 → :func:`hash_api_key` → fresh store →
``set_user_key_hash`` → ``log_admin_operation`` → 200
``{username, rotated}`` with zero key material.

Since M5 (users-only hot reload, Design (c) phase 2) every successful
mutation additionally runs :func:`_refresh_pro_users` — the in-process
pro-user snapshot is swapped so the change takes effect on the next MCP
request **without a restart**, at the right layer: create/delete/rotate
change what authenticates, grant/revoke change what resolves (grants never
gate ``_require_auth_db_user``).  Accordingly the M3 ``restart_required``
field is gone from user mutations; only a failed post-write refresh adds
``"reload": "failed"`` (the write itself stands — a restart remains the
operator fallback).  Grant/replace additionally attach
``"config_topology_restart_required": true`` when a referenced config is
absent from the frozen topology (CLI-added after startup); revoke never
does.

Plaintext keys are accepted only in request bodies, hashed with
:func:`hash_api_key`, and never stored, logged, echoed, or returned; key
hashes never leave the store through the API either (Design (f) hygiene
rules).
"""

from __future__ import annotations

import os
import sqlite3

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from caldav_mcp import config
from caldav_mcp.app_config import get_app_config
from caldav_mcp.audit import log_admin_operation, log_auth_attempt
from caldav_mcp.auth import (
    _FAILURE_MSG,
    _const_eq,
    _extract_key,
    _hdrs,
    _is_pro_mode,
    configure_pro_users,
)
from caldav_mcp.config_store import (
    ConfigStore,
    StoreConflictError,
    StoreError,
    StoreNotFoundError,
    StoreSchemaError,
    StoreValidationError,
    UserRecord,
)
from caldav_mcp.db_loader import ProUser
from caldav_mcp.errors import log
from caldav_mcp.key_hash import hash_api_key
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


async def _read_json_body(request: Request) -> dict[str, object] | JSONResponse:
    """Parse a JSON object request body (API-owned shape validation, Design (e)).

    Returns the decoded object on success, otherwise the 400 response for a
    wrong ``Content-Type``, malformed JSON, or a non-object body (edge-case
    table).  Only *shape* is checked here — store-domain rules (username
    non-empty, config-name charset) stay with the store, never duplicated.
    """
    content_type = request.headers.get("content-type", "")
    if content_type.split(";")[0].strip().lower() != "application/json":
        return JSONResponse({"error": "Content-Type must be application/json"}, status_code=400)
    try:
        body = await request.json()
    except ValueError:  # json.JSONDecodeError and friends (ValueError subclass)
        return JSONResponse({"error": "malformed JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)
    return body


# ---------------------------------------------------------------------------
# M5.1 — users-only hot reload (Design (c) phase 2)
# ---------------------------------------------------------------------------


def _refresh_pro_users() -> bool:
    """Swap the in-process pro-user snapshot so it matches the store (M5.1).

    Fresh :class:`ConfigStore` (Design (c) connection model) →
    ``list_users()`` → :class:`~caldav_mcp.db_loader.ProUser` tuple →
    :func:`caldav_mcp.auth.configure_pro_users` — one atomic global
    assignment, so in-flight requests already holding the previous tuple
    are unaffected.  Called after every successful user mutation; the
    frozen ``AppConfig`` topology is never touched (non-goal).

    Returns ``False`` after logging when the refresh could not run: the
    completed write stands, the caller reports ``"reload": "failed"``, and
    a restart remains the operator fallback (edge-case table).
    """
    try:
        with ConfigStore(_db_path()) as store:
            records = store.list_users()
        configure_pro_users(
            tuple(
                ProUser(
                    username=record.username,
                    key_hash=record.key_hash,
                    config_names=record.config_names,
                )
                for record in records
            )
        )
    except Exception as exc:
        log.error(
            "admin API: user snapshot refresh failed: %s: %s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return False
    return True


def _signal_reload(body: dict[str, object]) -> dict[str, object]:
    """Run the post-write refresh (M5.1) and attach its signal to *body*.

    On success *body* is returned unchanged — user mutations apply live, so
    M5 drops ``restart_required`` entirely.  A failed refresh adds
    ``"reload": "failed"`` (write stands; error logged inside the refresh).
    """
    if not _refresh_pro_users():
        body["reload"] = "failed"
    return body


def _topology_restart_required(config_names: tuple[str, ...]) -> bool:
    """True when any *config_name* is absent from the frozen topology (M5).

    Grant/replace of a config added via CLI **after** startup succeeds at
    the store and applies to auth live, but access still needs a restart —
    the frozen ``AppConfig`` does not know the config (edge-case table).
    Revoke never calls this: revocation never requires topology.
    """
    frozen = {cfg.name for cfg in get_app_config().configs}
    return any(name not in frozen for name in config_names)


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
# Write handlers (M3 routes + M4 rotation; M5 applies mutations live)
#
# Flow per the M3.1 block, updated by M5.1: guard → parse/validate body →
# fresh store (with the table-(d) pre-checks) → store call →
# log_admin_operation → _signal_reload (post-write snapshot refresh; a
# failed refresh attaches "reload": "failed" — the write stands) → JSON
# response.  "restart_required" is gone: user mutations take effect on the
# next MCP request.  Grant/replace additionally attach
# "config_topology_restart_required": true when a referenced config is
# missing from the frozen topology; revoke never does.  Every store error
# funnels through _map_store_error; the M4 rotation route follows Design (f).
# ---------------------------------------------------------------------------


async def _handle_create_user(request: Request) -> JSONResponse:
    """``POST /admin/users`` — create a user from ``{username, key}`` (201).

    The plaintext key is hashed here and never stored, logged, echoed, or
    returned (Design (f)); username emptiness and duplicate conflicts are
    the store's to reject (Design (e)) → 400 / 409.  M5: the new user
    authenticates on the next MCP request — no restart.
    """
    denied = _require_admin(request)
    if denied is not None:
        return denied
    parsed = await _read_json_body(request)
    if isinstance(parsed, JSONResponse):
        return parsed
    username = parsed.get("username")
    if not isinstance(username, str):
        return JSONResponse({"error": "username is required and must be a string"}, status_code=400)
    key = parsed.get("key")
    if not isinstance(key, str) or not key:
        # API-owned non-empty key check, mirroring config_cli._handle_user_add.
        return JSONResponse({"error": "key must be a non-empty string"}, status_code=400)
    client_ip = _client_ip(request)
    key_hash = hash_api_key(key)
    try:
        with ConfigStore(_db_path()) as store:
            store.create_user(username, key_hash)
    except Exception as exc:
        log_admin_operation(
            action="create_user", status="error", target_user=username, client_ip=client_ip
        )
        return _map_store_error(exc)
    log_admin_operation(
        action="create_user", status="ok", target_user=username, client_ip=client_ip
    )
    return JSONResponse(
        _signal_reload({"username": username, "configs": []}),
        status_code=201,
    )


async def _handle_delete_user(request: Request) -> JSONResponse:
    """``DELETE /admin/users/{username}`` — 200 + body (Risks: not 204).

    Grants cascade via the schema FK (``user_configs`` ON DELETE CASCADE);
    M5: the deleted user's authentication fails on the next MCP request —
    no restart.
    """
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
            store.delete_user(username)
    except Exception as exc:
        log_admin_operation(
            action="delete_user", status="error", target_user=username, client_ip=client_ip
        )
        return _map_store_error(exc)
    log_admin_operation(
        action="delete_user", status="ok", target_user=username, client_ip=client_ip
    )
    return JSONResponse(_signal_reload({"deleted": True, "username": username}))


async def _handle_grant_config(request: Request) -> JSONResponse:
    """``POST /admin/users/{username}/configs`` — grant (200, idempotent).

    The store's ``grant_config`` owns existence/charset checks and the
    single-passthrough constraint (→ 404 / 400); re-granting is a no-op.
    M5: the grant applies to addressing live — but when the config is
    missing from the frozen topology (CLI-added after startup) the response
    carries ``config_topology_restart_required: true`` and access still
    needs a restart (edge-case table).
    """
    denied = _require_admin(request)
    if denied is not None:
        return denied
    username = request.path_params.get("username", "")
    transport_error = _username_transport_error(username)
    if transport_error is not None:
        return JSONResponse({"error": transport_error}, status_code=400)
    parsed = await _read_json_body(request)
    if isinstance(parsed, JSONResponse):
        return parsed
    config_name = parsed.get("config")
    if not isinstance(config_name, str):
        return JSONResponse({"error": "config is required and must be a string"}, status_code=400)
    client_ip = _client_ip(request)
    try:
        with ConfigStore(_db_path()) as store:
            store.grant_config(username, config_name)
            record = store.get_user(username)
    except Exception as exc:
        log_admin_operation(
            action="grant_config", status="error", target_user=username, client_ip=client_ip
        )
        return _map_store_error(exc)
    log_admin_operation(
        action="grant_config", status="ok", target_user=username, client_ip=client_ip
    )
    body = _user_payload(record)
    if _topology_restart_required((config_name,)):
        body["config_topology_restart_required"] = True
    return JSONResponse(_signal_reload(body))


async def _handle_revoke_config(request: Request) -> JSONResponse:
    """``DELETE /admin/users/{username}/configs/{config}`` — 200 + body.

    Pre-checks ``store.get_user`` → 404 for an unknown user (the store's
    ``revoke_config`` never raises ``StoreNotFoundError`` itself); revoking a
    never-granted config is an idempotent no-op (store parity).  M5: the
    revocation applies live — the config's dotted paths stop resolving on
    the next request — and the response never carries the topology flag
    (revocation never requires topology).
    """
    denied = _require_admin(request)
    if denied is not None:
        return denied
    username = request.path_params.get("username", "")
    transport_error = _username_transport_error(username)
    if transport_error is not None:
        return JSONResponse({"error": transport_error}, status_code=400)
    config_name = request.path_params.get("config", "")
    client_ip = _client_ip(request)
    try:
        with ConfigStore(_db_path()) as store:
            store.get_user(username)  # pre-check → StoreNotFoundError → 404
            store.revoke_config(username, config_name)
    except Exception as exc:
        log_admin_operation(
            action="revoke_config", status="error", target_user=username, client_ip=client_ip
        )
        return _map_store_error(exc)
    log_admin_operation(
        action="revoke_config", status="ok", target_user=username, client_ip=client_ip
    )
    body: dict[str, object] = {
        "revoked": True,
        "username": username,
        "config": config_name,
    }
    return JSONResponse(_signal_reload(body))


async def _handle_replace_configs(request: Request) -> JSONResponse:
    """``PUT /admin/users/{username}/configs`` — replace-all (200).

    Pre-checks every requested name against ``store.list_configs`` so a
    missing config yields a precise 404 naming the missing ones; 409 is
    therefore reserved for a genuine FK race (a config deleted between the
    pre-check and the write).  Single-passthrough violations surface as 400
    via M1's in-store enforcement inside ``set_user_configs``'s transaction.
    M5: the replacement applies live; when any referenced config is absent
    from the frozen topology the response carries
    ``config_topology_restart_required: true`` (grant semantics).  An empty
    replacement references no config, so — like revoke — it never carries
    the flag.
    """
    denied = _require_admin(request)
    if denied is not None:
        return denied
    username = request.path_params.get("username", "")
    transport_error = _username_transport_error(username)
    if transport_error is not None:
        return JSONResponse({"error": transport_error}, status_code=400)
    parsed = await _read_json_body(request)
    if isinstance(parsed, JSONResponse):
        return parsed
    configs = parsed.get("configs")
    if not isinstance(configs, list):
        return JSONResponse({"error": "configs is required and must be a list"}, status_code=400)
    if any(not isinstance(name, str) for name in configs):
        return JSONResponse({"error": "configs must be a list of strings"}, status_code=400)
    # Set semantics: dedupe (first-seen order) so duplicate entries cannot
    # surface as an IntegrityError→409 — 409 stays reserved for FK races.
    unique_configs: list[str] = list(dict.fromkeys(configs))
    client_ip = _client_ip(request)
    try:
        with ConfigStore(_db_path()) as store:
            known = set(store.list_configs())
            missing = [name for name in unique_configs if name not in known]
            if missing:
                raise StoreNotFoundError(
                    "config not found: " + ", ".join(repr(name) for name in missing)
                )
            store.set_user_configs(username, unique_configs)
            record = store.get_user(username)
    except Exception as exc:
        log_admin_operation(
            action="set_user_configs",
            status="error",
            target_user=username,
            client_ip=client_ip,
        )
        return _map_store_error(exc)
    log_admin_operation(
        action="set_user_configs", status="ok", target_user=username, client_ip=client_ip
    )
    body = _user_payload(record)
    if _topology_restart_required(tuple(unique_configs)):
        body["config_topology_restart_required"] = True
    return JSONResponse(_signal_reload(body))


async def _handle_rotate_key(request: Request) -> JSONResponse:
    """``PUT /admin/users/{username}/key`` — rotate a user's API key (200).

    Design (f) flow: guard → username transport guard → parse body →
    reject empty key 400 → :func:`hash_api_key` → fresh store →
    ``set_user_key_hash`` → ``log_admin_operation`` → 200
    ``{username, rotated}``.  The plaintext key appears
    only in the request body — never in a query string, log, audit entry,
    error message, or response — and the new hash never leaves the store
    either.  Unknown user → 404 via the single choke point.  M5: the new
    key authenticates (and the old one stops) on the next MCP request —
    no restart.
    """
    denied = _require_admin(request)
    if denied is not None:
        return denied
    username = request.path_params.get("username", "")
    transport_error = _username_transport_error(username)
    if transport_error is not None:
        return JSONResponse({"error": transport_error}, status_code=400)
    parsed = await _read_json_body(request)
    if isinstance(parsed, JSONResponse):
        return parsed
    key = parsed.get("key")
    if not isinstance(key, str) or not key:
        # API-owned non-empty key check, mirroring config_cli._handle_user_add.
        return JSONResponse({"error": "key must be a non-empty string"}, status_code=400)
    try:
        # API-owned shape check: a lone-surrogate key (valid JSON, invalid
        # UTF-8) would make hashing raise and surface as a bare 500.
        key.encode("utf-8")
    except UnicodeEncodeError:
        return JSONResponse({"error": "key must be valid UTF-8"}, status_code=400)
    client_ip = _client_ip(request)
    key_hash = hash_api_key(key)
    try:
        with ConfigStore(_db_path()) as store:
            store.set_user_key_hash(username, key_hash)
    except Exception as exc:
        log_admin_operation(
            action="rotate_key", status="error", target_user=username, client_ip=client_ip
        )
        return _map_store_error(exc)
    log_admin_operation(action="rotate_key", status="ok", target_user=username, client_ip=client_ip)
    return JSONResponse(_signal_reload({"username": username, "rotated": True}))


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def configure_admin_routes(mcp: FastMCP) -> None:
    """Register the admin REST routes on *mcp* (called from ``server.py``).

    Registers the two M2 read routes, the five M3 write routes, and the M4
    key-rotation route.  Must be called before ``run_http_async`` — fastmcp
    merges additional routes into the streamable-http app with lowest
    precedence (M2.1 spike).
    """
    # Read (M2).
    mcp.custom_route("/admin/users", methods=["GET"])(_handle_list_users)
    mcp.custom_route("/admin/users/{username}", methods=["GET"])(_handle_get_user)
    # Write (M3.1 routes) — applied live since M5 (via _signal_reload).
    mcp.custom_route("/admin/users", methods=["POST"])(_handle_create_user)
    mcp.custom_route("/admin/users/{username}", methods=["DELETE"])(_handle_delete_user)
    mcp.custom_route("/admin/users/{username}/configs", methods=["POST"])(_handle_grant_config)
    mcp.custom_route("/admin/users/{username}/configs", methods=["PUT"])(_handle_replace_configs)
    mcp.custom_route("/admin/users/{username}/configs/{config}", methods=["DELETE"])(
        _handle_revoke_config
    )
    # Key rotation (M4.1, Design (f)).
    mcp.custom_route("/admin/users/{username}/key", methods=["PUT"])(_handle_rotate_key)
