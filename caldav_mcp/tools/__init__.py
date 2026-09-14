"""MCP tool handlers for the caldav-mcp server.

Handlers are split across submodules by responsibility:

- ``queries``  – read-only calendar/event queries
- ``mutations`` – event create/update/delete/move
- ``attendees`` – attendee management

This module owns the shared ``with_caldav_client`` decorator, result helpers,
and re-exports every ``@mcp.tool()`` handler so that
``from caldav_mcp.tools import caldav_list_calendars`` keeps working.

Error Handling Strategy
-----------------------
Every handler returns a structured :class:`ToolResult`.  There are two layers:

1. **Decorator layer** — ``with_caldav_client`` catches ``_REMOTE_ERRORS``
   (auth failures, network errors, SSL errors) and returns a classified
   ``ToolResult`` via ``_render_error()``.

2. **Tool-specific validation** — Handlers return ``ToolResult.failure()``
   directly for input validation errors (bad priority, invalid RRULE, etc.)
   that are NOT remote errors.

Callers and tests branch on ``result.status`` / ``result.data``, never on
human-readable text.
"""

import inspect
import ssl
import time
from collections.abc import Callable
from typing import Any

import requests.exceptions
from caldav import DAVClient  # type: ignore[attr-defined]
from caldav.lib.error import DAVError

from caldav_mcp import mcp as mcp  # noqa: F401  (re-exported)
from caldav_mcp.addressing import parse_dotted_path, resolve_addressed_calendar
from caldav_mcp.audit import log_error, log_operation
from caldav_mcp.auth import (
    _authenticate,
    _is_pro_mode,
    _resolve_credentials,
)
from caldav_mcp.auth import (
    _get_client_ip as _get_client_ip,  # noqa: F401  (re-exported)
)
from caldav_mcp.auth import (
    _require_auth as _require_auth,  # noqa: F811  (re-exported for test patching)
)
from caldav_mcp.calendar import _get_calendar
from caldav_mcp.client_cache import get_cache
from caldav_mcp.config import (
    CALDAV_VERIFY_SSL,
    HDR_PASSWORD,
    HDR_URL,
    HDR_USERNAME,
    READ_ONLY,
)
from caldav_mcp.errors import (
    AuthError,
    NotFoundError,
    Status,
    ToolResult,
    _render_error,
)

# ---------------------------------------------------------------------------
# Errors considered "expected" CalDAV/transport failures.
# ---------------------------------------------------------------------------
_REMOTE_ERRORS = (
    AuthError,
    NotFoundError,
    DAVError,
    requests.exceptions.RequestException,
    ssl.SSLError,
    ValueError,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ok(message: str = "", data=None) -> ToolResult:
    """Shortcut for ``ToolResult.success`` — used by every happy-path return."""
    return ToolResult.success(message=message, data=data)


def _empty(message: str = "") -> ToolResult:
    """Shortcut for ``ToolResult.empty`` — used when a query returns no results."""
    return ToolResult.empty(message=message)


# Parameters always injected by the decorator — excluded from the public signature.
_ALWAYS_INJECTED = frozenset({"client", "pro_user", "write"})


def _filter_public_params(
    sig: inspect.Signature,
    needs_calendar: bool,
) -> list[inspect.Parameter]:
    """Return the parameters visible to FastMCP (excluding injected ones).

    ``client`` and ``pro_user`` are always excluded.  ``cal`` is excluded
    only when *needs_calendar* is ``True`` (the decorator injects it).
    ``write`` is a decorator-only flag, never exposed.
    """
    return [
        p
        for name, p in sig.parameters.items()
        if name not in _ALWAYS_INJECTED and (not needs_calendar or name != "cal")
    ]


def _build_wrapper_annotations(
    fn: Callable,
    needs_calendar: bool,
) -> dict[str, type]:
    """Build the annotation dict for the wrapper, excluding injected params."""
    annotations = {
        k: v
        for k, v in fn.__annotations__.items()
        if k != "return" and k not in _ALWAYS_INJECTED and (not needs_calendar or k != "cal")
    }
    annotations["return"] = fn.__annotations__.get("return")
    return annotations


def _resolve_client_and_calendar(
    needs_calendar: bool,
    kwargs: dict,
    pro_user=None,
) -> tuple[Any, Any] | ToolResult:
    """Resolve auth, create/cache DAVClient, optionally resolve calendar.

    **Simple mode** (``pro_user is None``): Credentials and remote identity
    come from the read-only config singleton (``caldav_mcp.app_config``);
    direct remotes carry env credentials, the passthrough remote carries
    per-request header credentials.

    **Pro mode** (``pro_user is not None``): The dotted path
    ``config.remote.calendar`` is parsed and resolved via
    :func:`~caldav_mcp.addressing.resolve_addressed_calendar`.  The client
    is built from the stored remote URL and credentials (direct) or from
    per-request ``X-Caldav-*`` headers (passthrough).

    Returns (client, cal_or_None) on success, or a :class:`ToolResult`
    failure when a calendar path is required but missing (read tools in
    pro mode without an explicit dotted path).

    Raises are caught by the caller's try/except.
    """
    from caldav_mcp.app_config import get_app_config

    if pro_user is not None:
        app = get_app_config()
        if app.mode == "db":
            path = kwargs.get("calendar_name") or kwargs.get("source_calendar") or ""

            if not path:
                return ToolResult.failure(
                    Status.ERROR,
                    "In pro mode this tool requires an explicit calendar "
                    "identifier in 'config.remote.calendar' format. "
                    "Please provide the full dotted path.",
                )

            resolution = resolve_addressed_calendar(app, pro_user, path)
            client = _resolve_pro_remote_client(resolution.remote)

            cal = None
            if needs_calendar:
                cal = _get_calendar(client, resolution.calendar_name)
            return client, cal

    # ── Simple mode (unchanged) ────────────────────────────────────────
    url, user, pw = _resolve_credentials()

    cache = get_cache()
    client = cache.get(url, user)
    if client is None:
        client = DAVClient(  # type: ignore[operator]
            url=url,
            username=user,
            password=pw,
            ssl_verify_cert=CALDAV_VERIFY_SSL,
        )
        cache.put(url, user, client)

    cal = None
    if needs_calendar:
        cal = _get_calendar(client, kwargs.get("calendar_name") or None)

    return client, cal


def mcp_tool_if_writable(annotations):
    """Apply @mcp.tool only when the server is not read-only.

    In read-only mode returns the identity decorator: the function stays
    defined and importable but is never registered on the MCP instance,
    so clients never see it in tools/list and cannot call it.
    """

    def decorator(fn):
        if READ_ONLY:
            return fn
        return mcp.tool(annotations=annotations)(fn)

    return decorator


def _resolve_pro_remote_client(remote):
    """Build or reuse a DAVClient for the given pro-mode *remote*.

    For direct remotes, the stored URL and credentials are used.
    For passthrough remotes, per-request ``X-Caldav-*`` headers supply the
    credentials.  The client is cached by ``(url, username)`` like any other.

    For passthrough clients, a keyed hash of the password is stored alongside
    the cached client and verified on each cache hit to prevent cross-user
    escalation (a different caller supplying the same username but a wrong
    password will not reuse another caller's cached client).

    Raises :class:`~caldav_mcp.errors.AuthError` when a passthrough remote
    is missing the required header credentials.
    """
    if remote.auth_mode == "passthrough":
        from fastmcp.server.dependencies import get_http_headers

        headers = get_http_headers()
        url = headers.get(HDR_URL, "")
        hdr_username = headers.get(HDR_USERNAME, "")
        pw = headers.get(HDR_PASSWORD, "")
        if not url or not hdr_username or not pw:
            raise AuthError(
                "Missing CalDAV credentials. Provide the X-Caldav-Url, "
                "X-Caldav-Username, and X-Caldav-Password headers."
            )
        username = hdr_username
        # Verify password on cache hit to prevent cross-user escalation.
        cache = get_cache()
        client = cache.get_with_password(url, username, pw)
        if client is not None:
            return client
        client = DAVClient(  # type: ignore[operator]
            url=url,
            username=username,
            password=pw,
            ssl_verify_cert=CALDAV_VERIFY_SSL,
        )
        cache.put(url, username, client, password=pw)
        return client
    else:
        url = remote.url
        username = remote.username
        pw = remote.password

    cache = get_cache()
    client = cache.get(url, username)
    if client is None:
        client = DAVClient(  # type: ignore[operator]
            url=url,
            username=username,
            password=pw,
            ssl_verify_cert=CALDAV_VERIFY_SSL,
        )
        cache.put(url, username, client)
    return client


def with_caldav_client(needs_calendar=True, write=False):
    """Decorator that handles auth, client creation, and error classification.

    The wrapped function receives ``client`` and optionally ``cal`` as injected
    keyword arguments.  The public signature exposed to FastMCP excludes these
    injected parameters.

    Parameters
    ----------
    needs_calendar : bool
        When ``True`` (default) a ``cal`` parameter is injected.
    write : bool
        When ``True`` the tool is a write tool.  In pro mode
        (``mode == "db"``) a dotted ``config.remote.calendar`` path is
        **required** in ``calendar_name`` — a plain name is rejected with a
        typed validation failure *before* any client or calendar resolution.
    """

    def decorator(fn):
        sig = inspect.signature(fn)
        public_params = _filter_public_params(sig, needs_calendar)

        def wrapper(*_args, **kwargs):
            start_time = time.monotonic()
            try:
                # ── Endpoint authentication ────────────────────────────
                # Simple mode: use globals() lookup so patching
                # caldav_mcp.tools._require_auth short-circuits here.
                # Pro mode: delegate to _authenticate() which routes
                # through _require_auth() so patching
                # caldav_mcp.auth._require_auth also short-circuits.
                if _is_pro_mode():
                    pro_user, error = _authenticate()
                    if error:
                        return error
                else:
                    auth_err = globals()["_require_auth"]()
                    if auth_err is not None:
                        return auth_err
                    pro_user = None

                # ── Pro-mode write-tool gate ───────────────────────────
                if write and pro_user is not None:
                    # Check calendar_name (most write tools) or source_calendar
                    # (caldav_move_event).
                    cal_name = kwargs.get("calendar_name") or kwargs.get("source_calendar") or ""
                    try:
                        parse_dotted_path(cal_name)
                    except ValueError:
                        return ToolResult.failure(
                            Status.ERROR,
                            "Pro mode requires an explicit calendar identifier "
                            "in 'config.remote.calendar' format. "
                            "Please provide the full dotted path.",
                        )

                # ── Client and calendar resolution ─────────────────────
                resolved = _resolve_client_and_calendar(
                    needs_calendar,
                    kwargs,
                    pro_user=pro_user,
                )
                # Early return when _resolve_client_and_calendar yields a
                # ToolResult (e.g. missing dotted path in pro-mode reads).
                if isinstance(resolved, ToolResult):
                    return resolved
                client, cal = resolved

                # ── Handler invocation ─────────────────────────────────
                # Pass pro_user to handlers that accept it (e.g.
                # caldav_move_event for target resolution in pro mode).
                handler_kwargs = dict(kwargs)
                if "pro_user" in sig.parameters:
                    handler_kwargs["pro_user"] = pro_user
                if needs_calendar:
                    result = fn(client=client, cal=cal, **handler_kwargs)
                else:
                    result = fn(client=client, **handler_kwargs)

                duration_ms = (time.monotonic() - start_time) * 1000
                log_operation(
                    tool_name=fn.__name__,
                    status=result.status.value if hasattr(result, "status") else "unknown",
                    duration_ms=duration_ms,
                    calendar_name=kwargs.get("calendar_name", ""),
                )
                return result
            except _REMOTE_ERRORS as e:
                duration_ms = (time.monotonic() - start_time) * 1000
                log_error(fn.__name__, type(e).__name__, str(e))
                log_operation(
                    tool_name=fn.__name__,
                    status="error",
                    duration_ms=duration_ms,
                    calendar_name=kwargs.get("calendar_name", ""),
                )
                return _render_error(e, fn.__name__)

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        wrapper.__annotations__ = _build_wrapper_annotations(fn, needs_calendar)
        wrapper.__signature__ = sig.replace(parameters=public_params)
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Re-export all tool handlers for backward compatibility
# ---------------------------------------------------------------------------
from caldav_mcp.tools.attendees import (  # noqa: E402, F401
    caldav_add_attendee,
    caldav_list_attendees,
    caldav_remove_attendee,
)
from caldav_mcp.tools.mutations import (  # noqa: E402, F401
    caldav_create_event,
    caldav_delete_event,
    caldav_move_event,
    caldav_update_event,
)
from caldav_mcp.tools.queries import (  # noqa: E402, F401
    caldav_get_event_by_uid,
    caldav_get_events,
    caldav_get_freebusy,
    caldav_get_today_events,
    caldav_get_week_events,
    caldav_list_calendars,
    caldav_search_events,
)
