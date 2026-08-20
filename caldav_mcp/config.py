"""Environment parsing and server-timezone resolution.

Owns the config constants read from the environment and computed once at import
time. Runtime consumers reference these through the ``server`` namespace so they
observe the same values that tests patch.
"""

import logging
import os
from datetime import UTC, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

# NOTE: resolved once at import time — env changes after startup are ignored.
DEFAULT_PORT = int(os.environ.get("CALDAV_MCP_PORT", "8080"))
DEFAULT_PATH = os.environ.get("CALDAV_MCP_PATH", "/mcp")

API_KEY = os.environ.get("CALDAV_MCP_API_KEY", "")

# HTTP header names — lowercase for case-insensitive dict lookups.
# These match the FastMCP / Starlette convention of lowercasing headers.
HDR_URL = "x-caldav-url"
HDR_USERNAME = "x-caldav-username"
HDR_PASSWORD = "x-caldav-password"
HDR_AUTHORIZATION = "authorization"
HDR_API_KEY = "x-api-key"


def _server_tz() -> tzinfo:
    """Return the configured server timezone.

    Reads the TZ environment variable (e.g. 'Europe/Vienna'); falls back to
    UTC when TZ is unset, empty, or invalid.
    """
    tz_name = os.environ.get("TZ", "").strip()
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("Unknown timezone %r, falling back to UTC", tz_name)
            return ZoneInfo("UTC")
    return UTC


SERVER_TZ = _server_tz()

# Rate limiting for authentication attempts
RATE_LIMIT_MAX_FAILURES = int(os.environ.get("CALDAV_MCP_RATE_LIMIT_MAX_FAILURES", "10"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("CALDAV_MCP_RATE_LIMIT_WINDOW_SECONDS", "60"))

# TLS / HTTPS configuration
TLS_CERT_PATH = os.environ.get("CALDAV_MCP_TLS_CERT", "")
TLS_KEY_PATH = os.environ.get("CALDAV_MCP_TLS_KEY", "")
TLS_CA_BUNDLE = os.environ.get("CALDAV_MCP_TLS_CA_BUNDLE", "")
CALDAV_VERIFY_SSL = os.environ.get("CALDAV_MCP_CALDAV_VERIFY_SSL", "true").lower() in ("true", "1", "yes")

# Log format: "text" or "json"
LOG_FORMAT = os.environ.get("CALDAV_MCP_LOG_FORMAT", "text")
