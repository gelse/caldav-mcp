# Plan 04a — Add typed exceptions and structured logging helper

> Parent plan: [`04-broad-except-error-handling.md`](04-broad-except-error-handling.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Introduce typed exceptions (a "not found" class and a "server/auth fault" class) and a
central logger so that later sub-steps (04b, 04c) can distinguish error classes and log
tracebacks without repeating ad-hoc code. No tool body is changed in this step.

## Context you must know

- [`server.py`](server.py) currently defines [`CalDAVError`](server.py:31) and uses it only in
  [`_resolve_credentials()`](server.py:35) to report missing credentials. It is always caught as
  bare `Exception` by tool handlers, so class distinctions are currently meaningless.
- Every tool wraps its body in `except Exception as e: return "ERROR: %s" % e`, which swallows the
  traceback. This sub-step provides the logging facility to fix that, but does **not** yet touch
  the `except` blocks.
- Existing code style uses `%` formatting and a module-level `CalDAVError(Exception)` class
  ([`server.py:31`](server.py:31)).

## Chosen mechanism (do not deviate)

- Use the standard-library `logging` module. Import it at the top of [`server.py`](server.py:1).
- Add a module-level logger:
  ```python
  log = logging.getLogger("caldav-mcp")
  ```
- Add three exception classes at module scope (keep `CalDAVError` as the base for
  backwards-compat with `_resolve_credentials`):
  - `NotFoundError(CalDAVError)` — raised when an event/calendar is not found.
  - `AuthError(CalDAVError)` — raised on missing or invalid credentials.
  - (Optionally) `ServerError(CalDAVError)` — raised for unexpected internal faults.
- Client-facing messages use a status prefix so MCP clients can distinguish error classes:
  `ERROR:[not_found]`, `ERROR:[auth]`, `ERROR:[server]`. (Wiring of these prefixes into tool
  return values happens in 04b/04c; this step only defines the vocabulary.)

## Implementation steps

### Step 1 — Add imports

At the top of [`server.py`](server.py:1), add:

```python
import logging
```

Place it alphabetically among the stdlib imports (after `import asyncio` and before the
third-party imports).

### Step 2 — Add the module logger

After the existing exception class block, add:

```python
log = logging.getLogger("caldav-mcp")
```

### Step 3 — Add typed exception classes

Replace the single [`CalDAVError`](server.py:31) class with a hierarchy, keeping
`CalDAVError` as the base so [`_resolve_credentials`](server.py:35) continues to work:

```python
class CalDAVError(Exception):
    """Base class for all caldav-mcp operational errors."""


class AuthError(CalDAVError):
    """Raised when CalDAV credentials are missing or invalid."""


class NotFoundError(CalDAVError):
    """Raised when a requested calendar or event does not exist."""


class ServerError(CalDAVError):
    """Raised for unexpected internal faults that should be logged server-side."""
```

### Step 4 — (Recommended) add a logging helper

Add a small helper that logs an exception with a full traceback and returns a sanitized,
client-safe message. Place it below the exception classes:

```python
def _log_exception(exc: Exception, context: str) -> str:
    """Log an unexpected exception with traceback and return a safe client message.

    The returned message must never include credential material or raw exception
    strings that might leak secrets.
    """
    log.exception("Unhandled error in %s", context)
    return "ERROR:[server] Internal error"
```

Note: this helper is not wired into any tool in this step; it is available for 04b/04c.

## Definition of done

- [`server.py`](server.py) imports `logging`.
- A module-level `log` logger named `"caldav-mcp"` exists.
- `CalDAVError`, `AuthError`, `NotFoundError`, and `ServerError` classes exist, with
  `AuthError`/`NotFoundError`/`ServerError` subclassing `CalDAVError`.
- `_log_exception(exc, context)` (or equivalent) logs via `log.exception` and returns a
  client-safe message without credential material.
- The module imports cleanly (`python -c "import server"` succeeds).
- No tool function body is modified.

## Constraints / rules

- Do NOT modify any tool function, any `@mcp.tool()` body, or any existing `except` block.
- Do NOT modify [`_resolve_credentials`](server.py:35) beyond what is necessary; its current
  `raise CalDAVError(...)` behavior must remain valid.
- Do NOT add tests or documentation in this step.
- Do NOT introduce third-party logging libraries; use stdlib `logging`.
- Follow existing style: `%` formatting where logging format strings are used, concise names.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Add typed exceptions and logging helper
```
