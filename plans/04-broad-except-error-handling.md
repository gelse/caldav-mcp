# Plan: Replace broad `except Exception` with structured error handling

## Problem

Every tool wraps its body in `except Exception as e: return "ERROR: %s" % e`. This:
- swallows the stack trace (no server-side logging),
- conflates "not found" with genuine server faults,
- returns plain strings so MCP clients cannot distinguish error classes,
- makes `CalDAVError` ([`server.py:29`](../server.py:29)) pointless since all tools catch `Exception`.

## Goal

Return meaningful, distinguishable errors and log the underlying cause server-side, without
leaking sensitive credential details to clients.

## Steps

1. Add structured logging (e.g. `logging`/`loguru`) that records `traceback` for unexpected
   exceptions.
2. Raise typed exceptions (e.g. `CalDAVError`, `NotFoundError`, `AuthError`) and let FastMCP
   surface them as proper MCP errors, or return structured string results with a status prefix.
3. Only catch the specific exceptions needed for user-friendly messages (e.g. "not found"),
   and re-raise/report the rest.
4. Ensure error messages never echo passwords or raw credentials.
5. Update tests/docs as needed.

## Affected files

- `server.py` (all tool bodies)

## Acceptance criteria

- Unexpected errors are logged with a full traceback.
- Client-visible errors distinguish "not found", "auth failure", and "server error".
- No credential material appears in error messages.
