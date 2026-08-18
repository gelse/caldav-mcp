"""Thin entrypoint for the caldav-mcp server.

All logic lives in the :mod:`caldav_mcp` package; this module exists to keep the
installed ``caldav-mcp`` console script (``server:main``) and existing imports
stable.  It re-exports every name the package exposes so ``import server;
server.<name>`` (and ``mock.patch.object(server, ...)``) continue to work, and
provides the ``main()`` entrypoint that launches the FastMCP HTTP server.
"""

import asyncio
import os  # noqa: F401  (re-exported; tests assert ``server.os.environ``)

from caldav import DAVClient  # noqa: F401  (re-exported)  # type: ignore[attr-defined]
from fastmcp.server.dependencies import get_http_headers  # noqa: F401  (re-exported)

from caldav_mcp import (
    API_KEY,
    DEFAULT_PATH,
    DEFAULT_PORT,
    HDR_API_KEY,
    HDR_AUTHORIZATION,
    HDR_PASSWORD,
    HDR_URL,
    HDR_USERNAME,
    SERVER_TZ,
    AuthError,
    CalDAVError,
    NotFoundError,
    ServerError,
    _attendee_str,
    _comp,
    _const_eq,
    _event_to_dict,
    _format_ical_dt,
    _get_calendar,
    _log_exception,
    _now,
    _parse_dt,
    _require_auth,
    _resolve_credentials,
    _server_tz,
    _start_of_day,
    _text,
    _text_single,
    _validate_priority,
    _validate_rrule,
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
    log,
    mcp,
)

__all__ = [
    "mcp",
    # errors
    "AuthError",
    "CalDAVError",
    "NotFoundError",
    "ServerError",
    "_log_exception",
    "log",
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
    # entrypoint
    "main",
]


def main() -> None:
    """Run the CalDAV MCP server over the streamable HTTP transport."""
    asyncio.run(
        mcp.run_http_async(
            host="0.0.0.0",
            port=DEFAULT_PORT,
            transport="streamable-http",
            path=DEFAULT_PATH,
        )
    )


if __name__ == "__main__":
    main()
