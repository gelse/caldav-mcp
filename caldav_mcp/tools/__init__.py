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
from collections.abc import Callable
from typing import Any

import requests.exceptions
from caldav import DAVClient  # type: ignore[attr-defined]
from caldav.lib.error import DAVError
from fastmcp.server.dependencies import get_http_headers

from caldav_mcp import mcp
from caldav_mcp.auth import _require_auth, _resolve_credentials
from caldav_mcp.calendar import _get_calendar
from caldav_mcp.client_cache import get_cache
from caldav_mcp.errors import (
    AuthError,
    NotFoundError,
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
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ok(message: str = "", data=None) -> ToolResult:
    return ToolResult.success(message=message, data=data)


def _empty(message: str = "") -> ToolResult:
    return ToolResult.empty(message=message)


def _filter_public_params(
    sig: inspect.Signature,
    needs_calendar: bool,
) -> list[inspect.Parameter]:
    """Return the parameters visible to FastMCP (excluding injected client/cal)."""
    return [
        p
        for name, p in sig.parameters.items()
        if name != "client" and (not needs_calendar or name != "cal")
    ]


def _build_wrapper_annotations(
    fn: Callable,
    needs_calendar: bool,
) -> dict[str, type]:
    """Build the annotation dict for the wrapper, excluding injected params."""
    annotations = {
        k: v
        for k, v in fn.__annotations__.items()
        if k != "return" and k != "client" and (not needs_calendar or k != "cal")
    }
    annotations["return"] = fn.__annotations__.get("return")
    return annotations


def _resolve_client_and_calendar(
    needs_calendar: bool,
    kwargs: dict,
) -> tuple[Any, Any]:
    """Resolve auth, create/cache DAVClient, optionally resolve calendar.

    Returns (client, cal_or_None).
    Raises are caught by the caller's try/except.
    """
    url, user, pw = _resolve_credentials()

    cache = get_cache()
    client = cache.get(url, user)
    if client is None:
        client = DAVClient(url=url, username=user, password=pw)  # type: ignore[operator]
        cache.put(url, user, client)

    cal = None
    if needs_calendar:
        cal = _get_calendar(client, kwargs.get("calendar_name") or None)

    return client, cal


def with_caldav_client(needs_calendar=True):
    """Decorator that handles auth, client creation, and error classification.

    The wrapped function receives ``client`` and optionally ``cal`` as injected
    keyword arguments.  The public signature exposed to FastMCP excludes these
    injected parameters.
    """

    def decorator(fn):
        sig = inspect.signature(fn)
        public_params = _filter_public_params(sig, needs_calendar)

        def wrapper(*_args, **kwargs):
            try:
                error = _require_auth()
                if error:
                    return error
                client, cal = _resolve_client_and_calendar(needs_calendar, kwargs)
                if needs_calendar:
                    return fn(client=client, cal=cal, **kwargs)
                return fn(client=client, **kwargs)
            except _REMOTE_ERRORS as e:
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
from caldav_mcp.tools.queries import (  # noqa: E402, F401
    caldav_get_events,
    caldav_get_event_by_uid,
    caldav_get_freebusy,
    caldav_get_today_events,
    caldav_get_week_events,
    caldav_list_calendars,
    caldav_search_events,
)
from caldav_mcp.tools.mutations import (  # noqa: E402, F401
    caldav_create_event,
    caldav_delete_event,
    caldav_move_event,
    caldav_update_event,
)
from caldav_mcp.tools.attendees import (  # noqa: E402, F401
    caldav_add_attendee,
    caldav_list_attendees,
    caldav_remove_attendee,
)
