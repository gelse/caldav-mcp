"""Structured audit logging for CalDAV MCP operations.

Provides functions for logging authentication attempts, tool executions,
and errors in a structured JSON format suitable for log aggregation systems.

SECURITY: Never log token values, passwords, or full Authorization headers.
Only log success/failure status and method type.
"""

import json
import logging
import time

_audit_log = logging.getLogger("caldav-mcp.audit")


def log_auth_attempt(
    success: bool,
    client_ip: str = "unknown",
    method: str = "bearer",
    reason: str = "",
) -> None:
    """Log an authentication attempt.

    Parameters
    ----------
    success : bool
        Whether authentication succeeded.
    client_ip : str
        IP address of the requester (best-effort; may be proxied).
    method : str
        Auth method used: 'bearer', 'api-key', or 'none'.
    reason : str
        Failure reason, if applicable.
    """
    entry = {
        "event": "auth",
        "success": success,
        "client_ip": client_ip,
        "method": method,
        "reason": reason,
        "ts": time.time(),
    }
    if success:
        _audit_log.info(json.dumps(entry))
    else:
        _audit_log.warning(json.dumps(entry))


def log_operation(
    tool_name: str,
    status: str,
    duration_ms: float,
    calendar_name: str = "",
    detail: str = "",
) -> None:
    """Log a tool execution.

    Parameters
    ----------
    tool_name : str
        Name of the MCP tool handler.
    status : str
        Result status ('ok', 'empty', 'error', 'auth', 'not_found').
    duration_ms : float
        Wall-clock duration in milliseconds.
    calendar_name : str
        Calendar operated on, if applicable.
    detail : str
        Optional short detail (e.g. event UID).
    """
    entry = {
        "event": "tool",
        "tool": tool_name,
        "status": status,
        "duration_ms": round(duration_ms, 2),
        "calendar": calendar_name,
        "detail": detail,
        "ts": time.time(),
    }
    _audit_log.info(json.dumps(entry))


def log_error(tool_name: str, error_type: str, context: str) -> None:
    """Log an error with context.

    Parameters
    ----------
    tool_name : str
        Name of the MCP tool handler.
    error_type : str
        Exception class name.
    context : str
        Additional context about the error.
    """
    entry = {
        "event": "error",
        "tool": tool_name,
        "error_type": error_type,
        "context": context,
        "ts": time.time(),
    }
    _audit_log.warning(json.dumps(entry))
