"""Thin entrypoint for the caldav-mcp server.

All logic lives in the :mod:`caldav_mcp` package; this module exists to keep the
installed ``caldav-mcp`` console script (``server:main``) and existing imports
stable.  It re-exports every name the package exposes so ``import server;
server.<name>`` continues to work, and provides the ``main()`` entrypoint that
launches the FastMCP HTTP server.

Leaf modules (``auth``, ``calendar``, ``datetime_utils``) now import directly
from their owning modules, so the ``import server`` in this file no longer
creates a circular dependency for those modules.  ``tools.py`` still routes
through ``server.*`` for backward-compatible test mocking.
"""

import asyncio
import os  # noqa: F401  (re-exported; tests assert ``server.os.environ``)
from typing import Any

from caldav import (
    DAVClient,  # noqa: F401  (re-exported)  # type: ignore[attr-defined]  # caldav has no type stubs
)
from fastmcp.server.dependencies import get_http_headers  # noqa: F401  (re-exported)

from caldav_mcp import (
    API_KEY,
    CALDAV_VERIFY_SSL,
    DEFAULT_PATH,
    DEFAULT_PORT,
    HDR_API_KEY,
    HDR_AUTHORIZATION,
    HDR_PASSWORD,
    HDR_URL,
    HDR_USERNAME,
    RATE_LIMIT_MAX_FAILURES,
    RATE_LIMIT_WINDOW_SECONDS,
    SERVER_TZ,
    TLS_CA_BUNDLE,
    TLS_CERT_PATH,
    TLS_KEY_PATH,
    LOG_FORMAT,
    AuthError,
    CalDAVClient,
    CalDAVError,
    NotFoundError,
    ServerError,
    Status,
    ToolResult,
    _attendee_str,
    _comp,
    _const_eq,
    _event_to_dict,
    _format_ical_dt,
    _get_calendar,
    _get_client_ip,
    _log_exception,
    _now,
    _parse_dt,
    _render_error,
    _require_auth,
    _resolve_credentials,
    _server_tz,
    _start_of_day,
    _text,
    _text_single,
    _validate_priority,
    _validate_rrule,
    auth_rate_limiter,
    build_event,
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
    limit_string_length,
    log,
    log_auth_attempt,
    log_error,
    log_operation,
    mcp,
    parse_attendee_emails,
    RateLimiter,
    sanitize_text,
    validate_calendar_name,
    validate_email,
    MAX_CALENDAR_NAME_LENGTH,
    MAX_CATEGORIES_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_LOCATION_LENGTH,
    MAX_QUERY_LENGTH,
    MAX_SUMMARY_LENGTH,
)
from caldav_mcp.constants import (  # noqa: E402
    DEFAULT_ATTENDEE_ROLE,
    DEFAULT_PARTSTAT,
    DEFAULT_RSVP,
    ERR_INVALID_RRULE,
    ERR_NO_COMPONENT,
    ICAL_VERSION,
    MAILTO_PREFIX,
    PRODID,
    UID_DOMAIN,
)

__all__ = [
    "mcp",
    # security
    "log_auth_attempt",
    "log_error",
    "log_operation",
    "RateLimiter",
    "auth_rate_limiter",
    "sanitize_text",
    "validate_calendar_name",
    "validate_email",
    "limit_string_length",
    "MAX_SUMMARY_LENGTH",
    "MAX_LOCATION_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_CATEGORIES_LENGTH",
    "MAX_CALENDAR_NAME_LENGTH",
    "MAX_QUERY_LENGTH",
    "CALDAV_VERIFY_SSL",
    "RATE_LIMIT_MAX_FAILURES",
    "RATE_LIMIT_WINDOW_SECONDS",
    "TLS_CERT_PATH",
    "TLS_KEY_PATH",
    "TLS_CA_BUNDLE",
    "LOG_FORMAT",
    "_get_client_ip",
    # constants
    "DEFAULT_ATTENDEE_ROLE",
    "DEFAULT_PARTSTAT",
    "DEFAULT_RSVP",
    "ERR_INVALID_RRULE",
    "ERR_NO_COMPONENT",
    "ICAL_VERSION",
    "MAILTO_PREFIX",
    "PRODID",
    "UID_DOMAIN",
    # event_builder
    "build_event",
    "parse_attendee_emails",
    # types
    "CalDAVClient",
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


def _build_ssl_config() -> "dict[str, str] | None":
    """Build uvicorn SSL keyword args from TLS_CERT_PATH / TLS_KEY_PATH.

    Returns ``None`` when TLS is not configured (no cert or key path set).
    Returns a dict suitable for ``**uvicorn_config`` when TLS is configured.
    """
    cert = TLS_CERT_PATH
    key = TLS_KEY_PATH
    if not cert or not key:
        return None
    cfg: dict[str, str] = {
        "ssl_certfile": cert,
        "ssl_keyfile": key,
    }
    if TLS_CA_BUNDLE:
        cfg["ssl_ca_certs"] = TLS_CA_BUNDLE
    return cfg


def main() -> None:
    """Run the CalDAV MCP server over the streamable HTTP transport.

    Optionally wraps the socket with TLS when ``CALDAV_MCP_TLS_CERT`` and
    ``CALDAV_MCP_TLS_KEY`` environment variables are set.
    """
    ssl_cfg = _build_ssl_config()
    run_kwargs: dict[str, Any] = {
        "host": "0.0.0.0",
        "port": DEFAULT_PORT,
        "transport": "streamable-http",
        "path": DEFAULT_PATH,
    }
    if ssl_cfg:
        run_kwargs["uvicorn_config"] = ssl_cfg
    asyncio.run(mcp.run_http_async(**run_kwargs))


if __name__ == "__main__":
    main()
