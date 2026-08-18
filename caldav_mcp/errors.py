"""Typed error classes and server-side logging for caldav-mcp."""

import logging

log = logging.getLogger("caldav-mcp")


class CalDAVError(Exception):
    """Base class for all caldav-mcp operational errors."""


class AuthError(CalDAVError):
    """Raised when CalDAV credentials are missing or invalid."""


class NotFoundError(CalDAVError):
    """Raised when a requested calendar or event does not exist."""


class ServerError(CalDAVError):
    """Raised for unexpected internal faults that should be logged server-side."""


def _log_exception(exc: Exception, context: str) -> str:
    """Log an unexpected exception with traceback and return a safe client message.

    The returned message must never include credential material or raw exception
    strings that might leak secrets.
    """
    log.exception("Unhandled error in %s", context)
    return "ERROR:[server] Internal error"
