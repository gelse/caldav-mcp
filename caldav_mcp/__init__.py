"""caldav-mcp application package.

This package owns the shared ``mcp`` :class:`fastmcp.FastMCP` instance and the
runtime names used across modules.  It re-exports every public/private symbol
that the original monolithic :mod:`server` module exposed, so ``server.py`` can
remain a thin entrypoint while existing tests that patch ``server.<name>`` and
call the internal helpers keep working.
"""

from fastmcp import FastMCP

# Create the shared FastMCP instance first: :mod:`caldav_mcp.tools` does
# ``from caldav_mcp import mcp`` at import time and will fail if ``mcp`` is not
# already defined during the partial package initialisation.
mcp: FastMCP = FastMCP(
    "caldav-mcp",
    instructions=(
        "CalDAV calendar access tool. Can list calendars, search events, "
        "create/modify/delete events, manage attendees, and get time-based "
        "free/busy and today/week summaries."
    ),
)

# Import submodules to register the @mcp.tool() handlers and expose the helper
# names.  Order matters: errors/config/auth/datetime_utils/calendar carry no
# side effects that need mcp; tools registers the handlers on ``mcp``.
#
# Circular import note: leaf modules (auth, calendar, datetime_utils) now import
# directly from their owning modules (config, errors, fastmcp) instead of
# through the ``server`` namespace.  The only remaining ``import server`` is in
# tools.py which routes through ``server.*`` for backward-compatible test
# mocking.  This breaks the previous circular chain:
#   server.py → __init__.py → auth/calendar/datetime_utils → server
from caldav_mcp import (  # noqa: E402, F401  (submodule imports; see comment above)
    auth,
    calendar,
    config,
    datetime_utils,
    errors,
    tools,
)
from caldav_mcp.auth import (  # noqa: F401, E402
    _const_eq,
    _require_auth,
    _resolve_credentials,
)
from caldav_mcp.calendar import (  # noqa: F401, E402
    _attendee_str,
    _comp,
    _event_to_dict,
    _get_calendar,
    _text,
    _text_single,
    _validate_priority,
    _validate_rrule,
)
from caldav_mcp.client_cache import (  # noqa: E402
    ClientCache,
    client_cache,
    get_cache,
    set_cache,
)
from caldav_mcp.config import (  # noqa: E402
    API_KEY,
    DEFAULT_PATH,
    DEFAULT_PORT,
    HDR_API_KEY,
    HDR_AUTHORIZATION,
    HDR_PASSWORD,
    HDR_URL,
    HDR_USERNAME,
    SERVER_TZ,
    _server_tz,
)
from caldav_mcp.datetime_utils import (  # noqa: F401, E402
    _format_ical_dt,
    _now,
    _parse_dt,
    _start_of_day,
)

# Re-export the names server.py previously exposed at module level.  Doing this
# in __init__ lets server.py import a single flat set of names.
from caldav_mcp.errors import (  # noqa: E402
    AuthError,
    CalDAVError,
    NotFoundError,
    ServerError,
    Status,
    ToolResult,
    _log_exception,
    _render_error,
    log,
)
from caldav_mcp.tools import (  # noqa: F401, E402
    caldav_add_attendee,
    caldav_create_event,
    caldav_delete_event,
    caldav_get_event_by_uid,
    caldav_get_events,
    caldav_get_freebusy,
    caldav_get_today_events,
    caldav_get_week_events,
    caldav_list_attendees,
    caldav_list_calendars,
    caldav_move_event,
    caldav_remove_attendee,
    caldav_search_events,
    caldav_update_event,
)

__all__ = [
    "mcp",
    # errors
    "Status",
    "AuthError",
    "CalDAVError",
    "NotFoundError",
    "ServerError",
    "ToolResult",
    "_log_exception",
    "_render_error",
    "log",
    # client_cache
    "ClientCache",
    "client_cache",
    "get_cache",
    "set_cache",
    # config
    "API_KEY",
    "DEFAULT_PATH",
    "DEFAULT_PORT",
    "HDR_API_KEY",
    "HDR_AUTHORIZATION",
    "HDR_PASSWORD",
    "HDR_URL",
    "HDR_USERNAME",
    "SERVER_TZ",
    "_server_tz",
    # auth
    "_const_eq",
    "_require_auth",
    "_resolve_credentials",
    # datetime_utils
    "_format_ical_dt",
    "_now",
    "_parse_dt",
    "_start_of_day",
    # calendar
    "_attendee_str",
    "_comp",
    "_event_to_dict",
    "_get_calendar",
    "_text",
    "_text_single",
    "_validate_priority",
    "_validate_rrule",
    # tools
    "caldav_add_attendee",
    "caldav_create_event",
    "caldav_delete_event",
    "caldav_get_event_by_uid",
    "caldav_get_events",
    "caldav_get_freebusy",
    "caldav_get_today_events",
    "caldav_get_week_events",
    "caldav_list_attendees",
    "caldav_list_calendars",
    "caldav_move_event",
    "caldav_remove_attendee",
    "caldav_search_events",
    "caldav_update_event",
]
