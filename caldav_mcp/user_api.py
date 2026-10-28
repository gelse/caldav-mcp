"""User calendar provisioning REST route (pro mode) — ``POST /user/calendars``.

One authenticated call lets a pro-mode DB user provision their own CalDAV
calendar: the route builds the personal config (``user-<sanitized-username>``),
a ``direct`` remote whose password is encrypted with the caller's per-user
store secret (``u1:``-tagged token), the calendar row and the grant, then
applies the new topology **live** (re-load → swap the ``AppConfig`` singleton
and the pro-user snapshot) and returns the dotted ``config.remote.calendar``
path.  ``server.py`` registers the route inside the pro branch only; outside
pro mode the framework serves 404 and the handler re-checks fail-closed.

Guard order (mirrors ``caldav_mcp.admin`` with deliberate deviations):

1. ``_is_pro_mode()`` → 404 ``{"error": "not found"}`` (fail-closed defense
   in depth — registration already happens only in the pro branch).
2. ``auth._authenticate()`` → 401 ``{"error": err.message}`` for any typed
   auth failure (missing/wrong key, unknown user, rate-limited).  The
   limiter lives *inside* ``_authenticate`` (shared ``auth_rate_limiter``
   with the MCP tools), so — unlike admin's 429 — a rate-limited caller gets
   401 carrying the limiter's retry hint; re-checking the limiter here would
   double-count failures.  ``pro_user is None`` (concurrent snapshot swap
   after a successful verify) → 401, fail-closed.
3. Local ``_read_json_body`` → 400 (wrong Content-Type, malformed JSON,
   non-object body).  ``_read_json_body`` / ``_map_store_error`` are local
   copies of admin's helpers — importing admin's privates would couple the
   modules and mislabel their log lines.
4. Pre-validation before **any** store write: required non-empty string
   fields, http(s) URL with a host, and ``config_store._validate_name`` on
   ``calendar_name`` + the effective ``remote_name`` → 400 with the store's
   message (the store stays the backstop).
5. Personal config name derived from the authenticated username only (never
   from the body); an existing config granted to a *different* username →
   409 instead of silent cross-user sharing.

Store chain (order matters): ``create_config`` if absent → collision guard →
``grant_config`` → remote create/reuse/update → ``create_calendar`` **last**
(the grant always precedes the calendar write, so a duplicate calendar is a
genuine duplicate → 409, and an interrupted chain retries cleanly).  Remote
reuse compares ``(url, caldav_username)``: equal → decrypt the stored token
with the caller's user secret (same password → reuse unchanged; different →
``update_remote``; undecryptable → 409); unequal (including a passthrough
remote colliding on the name) → 409 — never silently attach to another
server.

**Serialization invariant**: the handler is ``async def`` and the entire
store chain runs as one synchronous block with **no ``await``** between the
collision guard and the grant — the single event loop therefore serializes
concurrent requests over the multi-transaction window (homelab-accepted
invariant; do not add awaits inside the chain).

Responses: 201 ``{"path", "config", "remote", "calendar"}`` — never a
password, token, or ``enc_secret`` — with ``"reload": "failed"`` appended
when the live apply could not run (the write stands; a restart is the
operator fallback).  Store/SQLite failures funnel through the local
``_map_store_error`` (400/404/409/503/500, ``{"error": ...}``).

Audit: exactly one ``log_admin_operation(action="user_add_calendar",
status="ok"|"error", target_user=..., client_ip=...)`` per *authenticated*
attempt.  Pre-auth failures are audited by the auth layer's own
``log_auth_attempt``; the non-pro 404 guard is silent (the route does not
exist outside pro deployments), matching the admin route's behavior.
"""

from __future__ import annotations

import os
import re
import sqlite3
from urllib.parse import urlparse

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from caldav_mcp.app_config import configure_app_config
from caldav_mcp.audit import log_admin_operation
from caldav_mcp.auth import _FAILURE_MSG, _authenticate, _is_pro_mode, configure_pro_users
from caldav_mcp.config_crypto import ConfigSecretError
from caldav_mcp.config_store import (
    ConfigStore,
    RemoteRecord,
    StoreConflictError,
    StoreError,
    StoreNotFoundError,
    StoreOpenError,
    StoreSchemaError,
    StoreValidationError,
    _validate_name,
)
from caldav_mcp.db_loader import (
    USER_TOKEN_PREFIX,
    _decrypt_with_secret,
    _encrypt_with_secret,
    load_pro_state,
)
from caldav_mcp.errors import log

_AUDIT_ACTION = "user_add_calendar"

_REQUIRED_FIELDS = ("caldav_url", "caldav_username", "caldav_password", "calendar_name")


class _ProvisionConflict(Exception):
    """A 409-level conflict detected inside the store chain.

    Raised for conflicts the store cannot express for this route: the
    personal config already belongs to another user, and an existing remote
    that matches by name but has different connection details (or an
    undecryptable stored password).  The duplicate-calendar conflict comes
    back from the store itself as ``StoreConflictError`` → 409.
    """


# ---------------------------------------------------------------------------
# Local helpers (copies of admin's, per plan D1.3 — do not import admin privates)
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    """Best-effort client IP for audit logging.

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


def _db_path() -> str:
    """Resolve the store path per request (lazy; validated at pro-mode startup)."""
    path = os.environ.get("CALDAV_MCP_DB_PATH", "").strip()
    if not path:
        raise StoreError("CALDAV_MCP_DB_PATH is not set")
    return path


async def _read_json_body(request: Request) -> dict[str, object] | JSONResponse:
    """Parse a JSON object request body (API-owned shape validation).

    Returns the decoded object on success, otherwise the 400 response for a
    wrong ``Content-Type``, malformed JSON, or a non-object body.  Only
    *shape* is checked here — field/domain rules live in
    :func:`_validate_fields` and the store.
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


def _map_store_error(exc: Exception) -> JSONResponse:
    """Single choke point: store/OS exception → HTTP error body."""
    if isinstance(exc, StoreValidationError):
        status_code, message = 400, str(exc)
    elif isinstance(exc, StoreNotFoundError):
        status_code, message = 404, str(exc)
    elif isinstance(exc, StoreConflictError):
        status_code, message = 409, str(exc)
    elif isinstance(exc, StoreSchemaError):
        status_code, message = 503, f"config store unavailable: {exc}"
    elif isinstance(exc, sqlite3.OperationalError):
        # Locked or unreadable store — carries the retry hint.
        status_code, message = 503, f"config store unavailable ({exc}) - retry shortly"
    elif isinstance(exc, StoreOpenError):
        # Typed open failure (permission/dir/…) — same retry-hint contract.
        status_code, message = 503, f"config store unavailable ({exc}) - retry shortly"
    elif isinstance(exc, StoreError):
        status_code, message = 503, f"config store unavailable: {exc}"
    else:
        # 500: generic message; the detail goes to the log only.
        log.error("user API unexpected error: %s: %s", type(exc).__name__, exc, exc_info=True)
        status_code, message = 500, "internal server error"
    return JSONResponse({"error": message}, status_code=status_code)


def _audit(username: str, client_ip: str, *, ok: bool) -> None:
    """Emit the single ``user_add_calendar`` audit entry for one attempt."""
    log_admin_operation(
        action=_AUDIT_ACTION,
        status="ok" if ok else "error",
        target_user=username,
        client_ip=client_ip,
    )


def _personal_config_name(username: str) -> str:
    """Derive the caller's personal config name from the *authenticated* username.

    ``"user-" + username`` with every character outside ``[A-Za-z0-9_-]``
    replaced by ``-``: always store-name-safe (charset regex, no dots,
    never empty), deterministic, and never taken from the request body.
    Distinct usernames can still sanitize to the same name — the collision
    guard turns that into an explicit 409 instead of silent sharing.
    """
    return "user-" + re.sub(r"[^A-Za-z0-9_-]", "-", username)


def _validate_fields(body: dict[str, object]) -> tuple[dict[str, str], JSONResponse | None]:
    """Validate the request body before any store write; return ``(fields, error)``.

    On success returns the extracted string fields (with the effective
    ``remote_name`` — defaulting to ``calendar_name``) and ``None``; on
    failure an empty dict and the 400 response.  Required fields must be
    non-empty, UTF-8-encodable strings (a lone surrogate is valid JSON but
    would blow up on encode *after* the config/grant writes — admin's
    rotate-key rule applied to every field), the URL must parse as http(s)
    with a host, and ``calendar_name`` / ``remote_name`` must pass the
    store's ``_validate_name`` (same message the store would produce).
    """
    fields: dict[str, str] = {}
    for key in _REQUIRED_FIELDS:
        value = body.get(key)
        if not isinstance(value, str) or not value:
            return {}, JSONResponse({"error": f"{key} must be a non-empty string"}, status_code=400)
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return {}, JSONResponse({"error": f"{key} must be valid UTF-8"}, status_code=400)
        fields[key] = value

    raw_remote = body.get("remote_name")
    if raw_remote is None:
        remote_name = fields["calendar_name"]  # plan D6 default
    elif isinstance(raw_remote, str):
        remote_name = raw_remote
    else:
        return {}, JSONResponse({"error": "remote_name must be a string"}, status_code=400)
    try:
        remote_name.encode("utf-8")
    except UnicodeEncodeError:
        return {}, JSONResponse({"error": "remote_name must be valid UTF-8"}, status_code=400)

    try:
        parsed_url = urlparse(fields["caldav_url"])
    except ValueError:
        # urlparse raises ValueError on structurally broken inputs (e.g.
        # "https://[::1/dav" → "Invalid IPv6 URL") — a 400, never a 500.
        return {}, JSONResponse(
            {"error": "caldav_url must be an http or https URL with a host"},
            status_code=400,
        )
    if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        return {}, JSONResponse(
            {"error": "caldav_url must be an http or https URL with a host"},
            status_code=400,
        )

    try:
        _validate_name(fields["calendar_name"], "calendar name")
        _validate_name(remote_name, "remote name")
    except StoreValidationError as exc:
        return {}, JSONResponse({"error": str(exc)}, status_code=400)

    fields["remote_name"] = remote_name
    return fields, None


# ---------------------------------------------------------------------------
# Store chain (synchronous — see the module docstring's serialization invariant)
# ---------------------------------------------------------------------------


def _provision(username: str, config_name: str, fields: dict[str, str]) -> dict[str, object]:
    """Run the store chain and return the 201 body.

    Order: read the per-user secret → ``create_config`` if absent →
    collision guard → ``grant_config`` → remote create/reuse/update →
    ``create_calendar`` last.  Synchronous throughout: the caller invokes
    this without awaiting in between, so the single event loop serializes
    the multi-transaction window.

    Raises ``_ProvisionConflict`` (→ 409) for cross-user config collisions
    and remote mismatches, ``StoreError`` when the per-user encryption
    secret is missing (→ 503), and the store's own exceptions otherwise.
    """
    calendar_name = fields["calendar_name"]
    remote_name = fields["remote_name"]

    with ConfigStore(_db_path()) as store:
        # Read first: a missing per-user secret (pre-v2 row that never got
        # backfilled) must fail before any write.  None → 503 via StoreError.
        user_secret = store.get_user_secret(username)
        if user_secret is None:
            raise StoreError("user encryption secret is missing for this user - retry shortly")

        # 1. Personal config: create when absent.
        try:
            snapshot = store.load_config(config_name)
        except StoreNotFoundError:
            store.create_config(config_name)
            snapshot = store.load_config(config_name)

        # 2. Collision guard: never share a personal config across users
        #    (e.g. two usernames sanitizing to the same name).
        other_grantees = [g for g in store.list_config_grants(config_name) if g != username]
        if other_grantees:
            raise _ProvisionConflict(
                f"config {config_name!r} is already provisioned for another user"
            )

        # 3. Grant BEFORE the calendar write (idempotent): an interrupted
        #    chain retries cleanly and a duplicate calendar below is a
        #    genuine duplicate — the grant cannot mask it.
        store.grant_config(username, config_name)

        # 4. Remote: create / reuse / password-refresh.
        token = USER_TOKEN_PREFIX + _encrypt_with_secret(fields["caldav_password"], user_secret)
        existing = next((r for r in snapshot.remotes if r.name == remote_name), None)
        if existing is None:
            store.create_remote(
                config_name,
                RemoteRecord(
                    config_name=config_name,
                    name=remote_name,
                    url=fields["caldav_url"],
                    auth_mode="direct",
                    username=fields["caldav_username"],
                    password_enc=token,
                ),
            )
        else:
            if (
                existing.auth_mode != "direct"
                or existing.url != fields["caldav_url"]
                or existing.username != fields["caldav_username"]
            ):
                # Unequal URL/username (incl. passthrough collision): never
                # silently attach the calendar to the wrong server.
                raise _ProvisionConflict(
                    f"remote {remote_name!r} already exists with different connection "
                    f"details - choose another remote_name"
                )
            try:
                stored_password = _decrypt_with_secret(
                    existing.password_enc.removeprefix(USER_TOKEN_PREFIX), user_secret
                )
            except ConfigSecretError as exc:
                raise _ProvisionConflict(
                    f"remote {remote_name!r} exists but its stored password cannot be "
                    f"decrypted - choose another remote_name"
                ) from exc
            if stored_password != fields["caldav_password"]:
                # Same server, rotated password: refresh the stored token.
                store.update_remote(
                    config_name,
                    remote_name,
                    RemoteRecord(
                        config_name=config_name,
                        name=remote_name,
                        url=fields["caldav_url"],
                        auth_mode="direct",
                        username=fields["caldav_username"],
                        password_enc=token,
                    ),
                )
            # else: byte-identical credentials → reuse the remote untouched.

        # 5. Calendar LAST: duplicate → StoreConflictError → 409.
        store.create_calendar(config_name, remote_name, calendar_name)

    return {
        "path": f"{config_name}.{remote_name}.{calendar_name}",
        "config": config_name,
        "remote": remote_name,
        "calendar": calendar_name,
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


async def _handle_add_calendar(request: Request) -> JSONResponse:
    """``POST /user/calendars`` — provision the caller's calendar (201)."""
    # 1. Fail-closed outside pro mode (registration is pro-branch-only too).
    if not _is_pro_mode():
        return JSONResponse({"error": "not found"}, status_code=404)

    # 2. Endpoint auth — the rate limiter lives inside _authenticate().
    pro_user, err = _authenticate()
    if err is not None:
        # Typed AUTH failures (missing/wrong key, unknown user, rate-limited
        # with retry hint) → 401, deliberately not admin's 429: the shared
        # auth_rate_limiter already counted the failure inside _authenticate.
        return JSONResponse({"error": err.message}, status_code=401)
    if pro_user is None:
        # Concurrent snapshot swap between verify and match — fail-closed.
        return JSONResponse({"error": _FAILURE_MSG}, status_code=401)

    username = pro_user.username
    client_ip = _client_ip(request)

    # 3. Body shape (local copy of admin's parser).
    parsed = await _read_json_body(request)
    if isinstance(parsed, JSONResponse):
        _audit(username, client_ip, ok=False)
        return parsed

    # 4. Pre-validation — before ANY store write.
    fields, invalid = _validate_fields(parsed)
    if invalid is not None:
        _audit(username, client_ip, ok=False)
        return invalid

    # 5.-7. Store chain (one synchronous block — no await in between) + 201 body.
    config_name = _personal_config_name(username)
    try:
        body = _provision(username, config_name, fields)
    except _ProvisionConflict as exc:
        _audit(username, client_ip, ok=False)
        return JSONResponse({"error": str(exc)}, status_code=409)
    except Exception as exc:
        _audit(username, client_ip, ok=False)
        return _map_store_error(exc)

    # 8. Live apply: rebuild from the store and swap the AppConfig singleton
    #    plus the pro-user snapshot so the new path resolves on the next MCP
    #    request without a restart.  Any failure (incl. a missing/empty
    #    CALDAV_MCP_CONFIG_SECRET env) never undoes the write — report
    #    "reload": "failed"; a restart remains the operator fallback.
    try:
        state = load_pro_state(_db_path(), os.environ["CALDAV_MCP_CONFIG_SECRET"].strip())
        configure_app_config(state.app_config)
        configure_pro_users(state.users)
    except Exception as exc:
        log.error("user API: live apply failed: %s: %s", type(exc).__name__, exc, exc_info=True)
        body["reload"] = "failed"

    _audit(username, client_ip, ok=True)
    return JSONResponse(body, status_code=201)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def configure_user_routes(mcp: FastMCP) -> None:
    """Register the user calendar provisioning route on *mcp* (from ``server.py``).

    Registers exactly ``POST /user/calendars`` — no read/list, delete, or
    rename routes (plan scope).  Must be called before ``run_http_async`` —
    fastmcp merges additional routes into the streamable-http app with
    lowest precedence (admin precedent).  Called only inside ``server.py``'s
    pro branch; the handler still re-checks pro mode fail-closed.
    """
    mcp.custom_route("/user/calendars", methods=["POST"])(_handle_add_calendar)
