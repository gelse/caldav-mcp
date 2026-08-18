# Plan 04b — Raise and catch typed exceptions in tool bodies

> Parent plan: [`04-broad-except-error-handling.md`](04-broad-except-error-handling.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Replace the blanket `except Exception as e: return "ERROR: %s" % e` blocks in every tool with
narrow, typed exception handling that (a) distinguishes "not found", "auth failure", and
"server error", and (b) logs unexpected errors with a full traceback via the helpers from
plan 04a. Assumes 04a is complete.

## Context you must know

- Plan 04a added `CalDAVError`, `AuthError`, `NotFoundError`, `ServerError`, a `log` logger, and
  `_log_exception(exc, context)` in [`server.py`](server.py).
- Every tool currently follows the pattern shown in
  [`caldav_list_calendars`](server.py:175) through [`caldav_search_events`](server.py:465):
  ```python
  try:
      ...
  except Exception as e:
      return "ERROR: %s" % e
  ```
- [`_resolve_credentials()`](server.py:35) raises `CalDAVError` on missing credentials. Because
  `AuthError` subclasses `CalDAVError`, the existing raise must be updated (below) to raise
  `AuthError` so the new catch branches can distinguish it.
- [`_get_calendar()`](server.py:53) raises `ValueError` for "no calendars" and "calendar not
  found". These should be translated to `NotFoundError` so handlers catch one class.

## Chosen mechanism (do not deviate)

- Client-facing result prefixes:
  - `ERROR:[not_found] ...` for missing calendar/event.
  - `ERROR:[auth] ...` for credential failures.
  - `ERROR:[server] ...` for everything else (with a logged traceback).
- For each tool, replace `except Exception as e` with three ordered clauses:
  ```python
  except AuthError as e:
      return "ERROR:[auth] %s" % e
  except NotFoundError as e:
      return "ERROR:[not_found] %s" % e
  except Exception as e:
      return _log_exception(e, "<tool-name>")
  ```
- Do NOT log credentials: `AuthError`/`NotFoundError` messages are returned directly (they are
  already sanitized), while truly unexpected errors go through `_log_exception`, which logs the
  traceback but returns only `ERROR:[server] Internal error`.

## Implementation steps

### Step 1 — Raise `AuthError` in `_resolve_credentials`

In [`_resolve_credentials()`](server.py:35), change `raise CalDAVError(...)` to
`raise AuthError(...)`, keeping the same message string. (The message is sanitized and mentions
env var names, not values.)

### Step 2 — Raise `NotFoundError` in `_get_calendar`

In [`_get_calendar()`](server.py:53):

- Replace `raise ValueError("No calendars found for this principal")` with
  `raise NotFoundError("No calendars found for this principal")`.
- Replace the `raise ValueError("Calendar '%s' not found. ..." % ...)` with
  `raise NotFoundError("Calendar '%s' not found. ..." % ...)` keeping the message identical.

### Step 3 — Update each tool's except block

For every `@mcp.tool()` function, replace the trailing `except Exception as e: return "ERROR: %s"
% e` with the following (substituting the correct tool name in the `_log_exception` context
argument):

```python
    except AuthError as e:
        return "ERROR:[auth] %s" % e
    except NotFoundError as e:
        return "ERROR:[not_found] %s" % e
    except Exception as e:
        return _log_exception(e, "caldav_<name>")
```

The affected functions, in order, are:

- [`caldav_list_calendars`](server.py:175) (context `caldav_list_calendars`)
- [`caldav_get_events`](server.py:189)
- [`caldav_get_event_by_uid`](server.py:233)
- [`caldav_create_event`](server.py:256)
- [`caldav_update_event`](server.py:333)
- [`caldav_add_attendee`](server.py:370)
- [`caldav_remove_attendee`](server.py:391)
- [`caldav_list_attendees`](server.py:416)
- [`caldav_delete_event`](server.py:432)
- [`caldav_move_event`](server.py:446)
- [`caldav_search_events`](server.py:465)
- [`caldav_get_freebusy`](server.py:487)

Note: `caldav_create_event` also contains embedded `try/except` blocks for `priority`
([`server.py:297`](server.py:297)) and `rrule` ([`server.py:306`](server.py:306)). These inner
blocks return `ERROR: priority ...` / `ERROR: invalid RRULE` strings directly. Leave them
unchanged in this step; do not convert those to typed exceptions yet.

## Definition of done

- [`_resolve_credentials`](server.py:35) raises `AuthError`.
- [`_get_calendar`](server.py:53) raises `NotFoundError` for both missing-calendars and
  specific-calendar-not-found cases.
- Every tool has an `except AuthError` → `ERROR:[auth]`, `except NotFoundError` →
  `ERROR:[not_found]`, and `except Exception` → `_log_exception(...)` clause, in that order.
- No bare `return "ERROR: %s" % e` remains in any tool except the intentional inner
  priority/rrule validation blocks in `caldav_create_event`.
- The module imports cleanly.

## Constraints / rules

- Do NOT change the inner `priority`/`rrule` validation `try/except` blocks in
  [`caldav_create_event`](server.py:256).
- Do NOT add or alter tests in this step (that is 04c).
- Do NOT change function signatures or the happy-path logic of any tool.
- Keep messages free of credential values; never interpolate `url`, `username`, or `password`
  into a returned or logged message.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Replace broad except with typed error handling
```
