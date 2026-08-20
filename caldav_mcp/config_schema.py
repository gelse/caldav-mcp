"""Pydantic models for startup configuration validation.

Validates environment variables at import time and raises clear errors
for invalid values.  Uses pydantic (available via fastmcp's transitive
dependency) so no new package is required.
"""

from __future__ import annotations

import logging
import os

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)


class ServerConfig(BaseModel):
    """Validated server configuration from environment variables."""

    port: int = 8080
    path: str = "/mcp"
    api_key: str = ""
    tz: str = ""

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError(f"Path must start with '/', got {v!r}")
        return v

    @field_validator("tz")
    @classmethod
    def validate_tz(cls, v: str) -> str:
        if not v:
            return v  # empty = UTC fallback, handled by _server_tz()
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"Unknown timezone {v!r}. Use an IANA name like 'Europe/Vienna'."
            ) from exc
        return v


class CalDAVConfig(BaseModel):
    """Validated CalDAV connection configuration."""

    url: str = ""
    username: str = ""
    password: str = ""

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v:
            return v  # empty is OK — may come from headers at runtime
        from urllib.parse import urlparse

        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"CalDAV URL must use http or https scheme, got {parsed.scheme!r}"
            )
        if not parsed.hostname:
            raise ValueError(f"CalDAV URL must have a hostname: {v!r}")
        return v


def load_server_config() -> ServerConfig:
    """Load and validate server configuration from environment."""
    return ServerConfig(
        port=int(os.environ.get("CALDAV_MCP_PORT", "8080")),
        path=os.environ.get("CALDAV_MCP_PATH", "/mcp"),
        api_key=os.environ.get("CALDAV_MCP_API_KEY", ""),
        tz=os.environ.get("TZ", ""),
    )


def load_caldav_config() -> CalDAVConfig:
    """Load and validate CalDAV configuration from environment."""
    return CalDAVConfig(
        url=os.environ.get("CALDAV_URL", ""),
        username=os.environ.get("CALDAV_USERNAME", ""),
        password=os.environ.get("CALDAV_PASSWORD", ""),
    )
