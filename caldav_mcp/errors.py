"""Typed error classes, structured tool results, and server-side logging.

This module owns the machine-readable ``ToolResult`` object that every
:mod:`caldav_mcp.tools` handler returns instead of a hand-formatted string.  A
small set of status codes (see :class:`Status`) replaces the ad-hoc
``ERROR:[auth]`` / ``OK:`` prefixes that were previously coupled to user-facing
text, so clients and tests can branch on a typed field rather than parsing
magic strings.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

log = logging.getLogger("caldav-mcp")


class CalDAVError(Exception):
    """Base class for all caldav-mcp operational errors."""


class AuthError(CalDAVError):
    """Raised when CalDAV credentials are missing or invalid."""


class NotFoundError(CalDAVError):
    """Raised when a requested calendar or event does not exist."""


class ServerError(CalDAVError):
    """Raised for unexpected internal faults that should be logged server-side."""


class Status(StrEnum):
    """Machine-readable result status used by every tool.

    Replaces the ad-hoc ``ERROR:[...]`` / ``OK:`` string prefixes with a small,
    well-defined set of codes so callers never have to parse text to learn the
    outcome of a tool call.
    """

    OK = "ok"
    EMPTY = "empty"
    AUTH = "auth"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass
class ToolResult:
    """Structured result returned by every :mod:`caldav_mcp.tools` handler.

    Attributes
    ----------
    status : Status
        Machine-readable outcome (see :class:`Status`).
    message : str
        Human-readable text describing the outcome.  This is decoupled from
        control flow; clients/tests branch on :attr:`status`, not on text.
    data : Any
        Optional structured payload (event dicts, event lists, etc.).
    """

    status: Status
    message: str = ""
    data: Any = None

    @property
    def ok(self) -> bool:
        """True when the call succeeded (ok or empty), i.e. not a failure."""
        return self.status in (Status.OK, Status.EMPTY)

    @classmethod
    def success(cls, message: str = "", data: Any = None) -> "ToolResult":
        """Build a successful result (``status=ok``)."""
        return cls(status=Status.OK, message=message, data=data)

    @classmethod
    def empty(cls, message: str = "") -> "ToolResult":
        """Build a successful-but-empty result (found/no-results state)."""
        return cls(status=Status.EMPTY, message=message)

    @classmethod
    def failure(cls, status: Status, message: str = "") -> "ToolResult":
        """Build a failure result for the given typed status."""
        return cls(status=status, message=message)

    def render(self) -> str:
        """Serialize to the final user-facing string.

        This is the *only* place a human-readable string is produced; control
        flow and tests branch on :attr:`status`/``data`` instead.
        """
        tag = {
            Status.OK: "OK",
            Status.EMPTY: "OK",
            Status.AUTH: "ERROR:[auth]",
            Status.NOT_FOUND: "ERROR:[not_found]",
            Status.ERROR: "ERROR:[server]",
        }[self.status]
        return f"{tag} {self.message}".strip()


def _render_error(exc: Exception, context: str) -> ToolResult:
    """Classify a caught exception into a structured :class:`ToolResult`.

    ``AuthError`` → ``Status.AUTH``, ``NotFoundError`` → ``Status.NOT_FOUND``,
    anything else is treated as an unexpected internal fault, logged server-side
    (never leaking secret/credential material), and returned as ``Status.ERROR``.
    """
    if isinstance(exc, AuthError):
        return ToolResult.failure(Status.AUTH, str(exc))
    if isinstance(exc, NotFoundError):
        return ToolResult.failure(Status.NOT_FOUND, str(exc))
    return _log_exception(exc, context)


def _log_exception(exc: Exception, context: str) -> ToolResult:
    """Log an unexpected exception and return a sanitized error result.

    The returned :class:`ToolResult` never includes raw exception strings or
    credential material that might leak secrets; the exact message is logged
    server-side via :meth:`logging.Logger.exception`.
    """
    log.exception("Unhandled error in %s", context)
    return ToolResult.failure(Status.ERROR, "Internal error")
