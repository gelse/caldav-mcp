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
from caldav_mcp.fanout import (
    AggregatedEntry as AggregatedEntry,
)
from caldav_mcp.fanout import (
    AggregatedResult as AggregatedResult,
)
from caldav_mcp.fanout import (
    RemoteScope,
    accessible_scopes,
    run_fanout,
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
# ``_query_fn`` is injected by ``with_caldav_fanout`` for pro-mode fan-out.
_ALWAYS_INJECTED = frozenset({"client", "pro_user", "write", "_query_fn"})


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


def _resolve_pro_client_for_scope(
    remote,
    credential_headers: dict[str, str],
):
    """Build or reuse a DAVClient for a fan-out scope.

    Shared helper used by :func:`caldav_mcp.fanout.run_fanout` and by
    :func:`_resolve_pro_remote_client`.  Avoids duplicating ``DAVClient``
    construction kwargs.

    For **direct** remotes the stored URL and credentials are used.
    For **passthrough** remotes the captured ``X-Caldav-*`` headers supply
    the credentials (missing headers raise :class:`AuthError`).

    Parameters
    ----------
    remote : Remote
        The remote to connect to.
    credential_headers : dict[str, str]
        Captured ``X-Caldav-*`` request headers (for passthrough remotes).

    Raises
    ------
    AuthError
        When a passthrough remote is missing the required header credentials.
    """
    cache = get_cache()

    if remote.auth_mode == "passthrough":
        url = credential_headers.get(HDR_URL, "")
        hdr_username = credential_headers.get(HDR_USERNAME, "")
        pw = credential_headers.get(HDR_PASSWORD, "")
        if not url or not hdr_username or not pw:
            raise AuthError(
                "Missing CalDAV credentials. Provide the X-Caldav-Url, "
                "X-Caldav-Username, and X-Caldav-Password headers."
            )
        username = hdr_username
        # Verify password on cache hit to prevent cross-user escalation.
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

    url = remote.url
    username = remote.username
    pw = remote.password
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


def with_caldav_fanout(needs_calendar=True, write=False, once_per_remote=False):
    """Decorator for read tools that fan out across remotes in pro mode.

    In **simple mode** delegates to the plain :func:`with_caldav_client` path
    unchanged (today's single-remote behavior).

    In **pro (DB) mode** runs ``_authenticate()``, builds scopes via
    :func:`~caldav_mcp.fanout.accessible_scopes`, executes
    :func:`~caldav_mcp.fanout.run_fanout`, and invokes ``log_operation`` once
    with ``calendar_name="<fanout:N scopes>"``.

    Parameters
    ----------
    needs_calendar : bool
        When ``True`` the fan-out resolves a calendar per ``(remote × calendar)``
        scope and injects it as ``cal``.
    write : bool
        Pro-mode write-tool gate (dotted path required).  Read fan-out tools
        leave this ``False``.
    once_per_remote : bool
        When ``True`` the tool expresses one query per remote rather than per
        calendar (``caldav_list_calendars`` yields one entry per remote;
        ``caldav_get_today_events`` / ``caldav_get_week_events`` fan out
        **once** — not once per level — and operate on the first accessible
        calendar of each remote).  Each entry is keyed by that calendar's name.
    """

    def decorator(fn):
        sig = inspect.signature(fn)
        public_params = _filter_public_params(sig, needs_calendar)

        def wrapper(*_args, **kwargs):
            start_time = time.monotonic()

            if _is_pro_mode():
                # ── Pro mode: fan-out across all accessible remotes ─────
                pro_user, auth_error = _authenticate()
                if auth_error:
                    return auth_error

                from caldav_mcp.app_config import get_app_config

                app = get_app_config()

                # ── Pro-mode write-tool gate ─────────────────────────────
                if write:
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

                # ── Credential headers for passthrough remotes ───────────
                from caldav_mcp.auth import _capture_credential_headers

                credential_headers = _capture_credential_headers()

                # ── Build scopes and filter by dotted-path ───────────────
                user_scopes = accessible_scopes(app, pro_user)

                # If a dotted-path calendar_name is given, restrict to
                # that single calendar's scope.
                calendar_filter = kwargs.get("calendar_name") or ""
                if calendar_filter:
                    try:
                        addr = parse_dotted_path(calendar_filter)
                    except ValueError:
                        return ToolResult.failure(
                            Status.ERROR,
                            "Expected calendar path in 'config.remote.calendar' format.",
                        )
                    # Restrict to the single addressed calendar.  Selection is
                    # on the remote, but the scope's calendar list must also be
                    # narrowed — otherwise the fan-out would query every
                    # calendar of that remote despite the explicit address.
                    user_scopes = tuple(
                        RemoteScope(
                            config_name=s.config_name,
                            remote=s.remote,
                            calendar_names=(addr.calendar_name,),
                        )
                        for s in user_scopes
                        if s.config_name == addr.config_name
                        and s.remote.name == addr.remote_name
                        and addr.calendar_name in s.calendar_names
                    )
                    # Replace the full dotted path with just the calendar
                    # name for the query function.
                    filter_cal = addr.calendar_name
                else:
                    filter_cal = ""

                # ── Run fan-out ──────────────────────────────────────────
                result = _execute_fanout(
                    fn,
                    user_scopes,
                    credential_headers,
                    filter_cal,
                    needs_calendar,
                    kwargs,
                    once_per_remote=once_per_remote,
                )

                duration_ms = (time.monotonic() - start_time) * 1000
                log_operation(
                    tool_name=fn.__name__,
                    status=(result.status.value if hasattr(result, "status") else "unknown"),
                    duration_ms=duration_ms,
                    calendar_name=f"<fanout:{len(user_scopes)} scopes>",
                )
                return result

            # ── Simple mode: delegate to with_caldav_client ──────────────
            try:
                auth_err = globals()["_require_auth"]()
                if auth_err is not None:
                    return auth_err

                # ── Client and calendar resolution ───────────────────────
                resolved = _resolve_client_and_calendar(needs_calendar, kwargs, pro_user=None)
                if isinstance(resolved, ToolResult):
                    return resolved
                client, cal = resolved

                # ── Build _query_fn for simple mode ──────────────────────
                if needs_calendar:

                    def _simple_query_fn(_scope, _cal_name):
                        return fn(client=client, cal=cal, **kwargs)
                else:

                    def _simple_query_fn(_scope, _cal_name):
                        return fn(client=client, **kwargs)

                # ── Handler invocation ───────────────────────────────────
                handler_kwargs = dict(kwargs)
                if "pro_user" in sig.parameters:
                    handler_kwargs["pro_user"] = None
                if "_query_fn" in sig.parameters:
                    handler_kwargs["_query_fn"] = _simple_query_fn
                # Remove _query_fn before calling the handler — it is a
                # decorator-injected artefact, not a real handler parameter.
                handler_kwargs.pop("_query_fn", None)
                if needs_calendar:
                    result = fn(client=client, cal=cal, **handler_kwargs)
                else:
                    result = fn(client=client, **handler_kwargs)

                duration_ms = (time.monotonic() - start_time) * 1000
                log_operation(
                    tool_name=fn.__name__,
                    status=(result.status.value if hasattr(result, "status") else "unknown"),
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


def _execute_fanout(
    fn,
    user_scopes,
    credential_headers,
    filter_cal,
    needs_calendar,
    kwargs,
    once_per_remote=False,
):
    """Execute the fan-out for a pro-mode read tool.

    Builds a per-scope ``query_fn`` that invokes *fn* with the resolved
    client and calendar, runs :func:`~caldav_mcp.fanout.run_fanout`, and
    converts the :class:`AggregatedResult` to a :class:`ToolResult`.

    When ``once_per_remote`` is ``True`` the number of calendars per scope is
    capped at one (``filter_cal`` if provided, else the first declared
    calendar) so day/week/list fan out exactly once instead of repeating an
    identical remote-wide query for every calendar.
    """
    from caldav_mcp.calendar import _get_calendar as _get_cal

    if once_per_remote:
        # Day/week/list tools express one query per remote.  Keep a single
        # (representative) calendar per scope; empty scopes produce a single
        # remote-level entry via the executor's cal=None path.
        user_scopes = tuple(
            RemoteScope(
                config_name=s.config_name,
                remote=s.remote,
                calendar_names=((filter_cal,) if filter_cal else s.calendar_names[:1]),
            )
            for s in user_scopes
        )

    # Handlers that accept an injected ``cal`` (day/week tools opt in via a
    # ``cal`` parameter while still declaring ``needs_calendar=False`` so the
    # simple-mode path resolves it internally).
    accepts_cal = "cal" in inspect.signature(fn).parameters

    def query_fn(scope: RemoteScope, cal_name: str | None):
        client = _resolve_pro_client_for_scope(scope.remote, credential_headers)
        cal = None
        if once_per_remote:
            if accepts_cal:
                cal_name = filter_cal or (scope.calendar_names[0] if scope.calendar_names else None)
                cal = _get_cal(client, cal_name)
        elif needs_calendar:
            cal = _get_cal(client, cal_name)

        handler_kwargs = dict(kwargs)
        # Remove calendar_name from handler kwargs — the fan-out provides
        # the resolved calendar directly.
        handler_kwargs.pop("calendar_name", None)
        # Remove _query_fn — it is a decorator-injected artefact, not a
        # real handler parameter.
        handler_kwargs.pop("_query_fn", None)

        if accepts_cal:
            result = fn(client=client, cal=cal, **handler_kwargs)
        else:
            result = fn(client=client, **handler_kwargs)

        # Wrap the handler's ToolResult into an AggregatedEntry so
        # run_fanout can aggregate across remotes.
        if hasattr(result, "status"):
            if result.status in (Status.OK, Status.EMPTY, Status.NOT_FOUND):
                entry = AggregatedEntry(
                    config_name=scope.config_name,
                    remote_name=scope.remote.name,
                    calendar_name=cal_name or "",
                    data=result.data,
                )
            else:
                entry = AggregatedEntry(
                    config_name=scope.config_name,
                    remote_name=scope.remote.name,
                    calendar_name=cal_name or "",
                    error=result.message or str(result.status),
                )
        else:
            entry = AggregatedEntry(
                config_name=scope.config_name,
                remote_name=scope.remote.name,
                calendar_name=cal_name or "",
                data=result,
            )
        return entry

    agg_result = run_fanout(user_scopes, credential_headers, query_fn)

    if agg_result.status == Status.EMPTY:
        return ToolResult.empty(message=agg_result.message or "No accessible calendars")

    if agg_result.status == Status.ERROR:
        return ToolResult.failure(Status.ERROR, agg_result.message)

    # Status.OK — build entries data list.
    entries_data = []
    for entry in agg_result.entries:
        entry_dict: dict[str, Any] = {
            "config_name": entry.config_name,
            "remote_name": entry.remote_name,
            "calendar_name": entry.calendar_name,
        }
        if entry.error:
            entry_dict["error"] = entry.error
        elif entry.data is not None:
            # Merge the handler's data into the entry dict.
            if isinstance(entry.data, dict):
                entry_dict.update(entry.data)
            elif isinstance(entry.data, list):
                entry_dict["data"] = entry.data
            else:
                entry_dict["data"] = entry.data
        entries_data.append(entry_dict)

    return ToolResult.success(
        message=agg_result.message,
        data=entries_data,
    )


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
