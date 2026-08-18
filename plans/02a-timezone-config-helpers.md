# Plan 02a: Introduce server timezone config and `_now`/`_start_of_day` helpers

## Context

This is **sub-step 02a** of the overall plan
[`02-timezone-utc-vs-local-fix.md`](./02-timezone-utc-vs-local-fix.md). The goal of the overall
plan is to make timezone handling explicit so "today"/"week" boundaries and date-only inputs
align with the intended timezone instead of being hardcoded to UTC.

This first sub-step establishes the foundation: a single module-level server-timezone value,
derived from the `TZ` environment variable, plus two small helper functions that later sub-steps
will use. Nothing else is changed yet.

## Current state

[`server.py`](../server.py) imports `datetime`, `timedelta`, `timezone` at
[`server.py:4`](../server.py:4) and hardcodes `timezone.utc` in multiple places. The container
already sets `TZ: Europe/Vienna` in [`docker-compose.yaml`](../docker-compose.yaml:14), but that
value is currently ignored.

## Change

All changes in this sub-step are confined to [`server.py`](../server.py).

1. Add `ZoneInfo` to the `datetime` import chain. At [`server.py:4`](../server.py:4), change:

   ```python
   from datetime import datetime, timedelta, timezone
   ```

   to:

   ```python
   from datetime import datetime, timedelta, timezone
   from zoneinfo import ZoneInfo
   ```

2. Immediately after the existing module-level constants block (after the `HDR_PASSWORD` definition
   at [`server.py:17`](../server.py:17)), add a module-level timezone constant and a helper that
   resolves it:

   ```python
   def _server_tz() -> timezone:
       """Return the configured server timezone.

       Reads the TZ environment variable (e.g. 'Europe/Vienna'); falls back to
       UTC when TZ is unset, empty, or invalid.
       """
       tz_name = os.environ.get("TZ", "").strip()
       if tz_name:
           try:
               return ZoneInfo(tz_name)
           except Exception:
               pass
       return timezone.utc


   SERVER_TZ = _server_tz()
   ```

   Notes:
   - The import must use `from zoneinfo import ZoneInfo` (Python 3.11+, already required by
     [`pyproject.toml`](../pyproject.toml:5)).
   - Use `os.environ.get("TZ", "")` exactly — do not introduce a new variable such as
     `CALDAV_MCP_TZ`.
   - Wrap the `ZoneInfo` construction in a `try/except Exception` (specifically this catches
     `ZoneInfoNotFoundError`) so an invalid `TZ` silently falls back to UTC without crashing at
     import time.
   - `SERVER_TZ` must be a module-level (uppercase) constant computed once.

3. Add a `_now()` helper and a `_start_of_day(dt)` helper. Place them just above the existing
   `_parse_dt` function (before [`server.py:68`](../server.py:68)):

   ```python
   def _now():
       """Return the current time in the server timezone."""
       return datetime.now(SERVER_TZ)


   def _start_of_day(dt):
       """Return the local midnight (start of day) for the given datetime in the server timezone."""
       return dt.replace(hour=0, minute=0, second=0, microsecond=0)
   ```

   Notes:
   - `_now()` uses `SERVER_TZ`, not `timezone.utc`.
   - `_start_of_day(dt)` only zeroes the time fields; it does not re-derive the timezone. It must
     be passed a timezone-aware datetime in the server timezone.

## Definition of done

- [`server.py`](../server.py) imports `zoneinfo` / `ZoneInfo`.
- [`server.py`](../server.py) defines `_server_tz()`, the module constant `SERVER_TZ`, `_now()`, and
  `_start_of_day()`.
- `python -c "import server; print(server.SERVER_TZ)"` runs without error and prints either the
  `TZ`-derived zone (when `TZ` is set) or `UTC` (when unset/invalid).
- No behavior of any existing tool has changed yet (helpers are not yet wired into call sites).

## Constraints

- Do **not** modify any file other than [`server.py`](../server.py) in this sub-step.
- Do **not** yet change `_parse_dt`, `caldav_get_events`, `caldav_get_today_events`,
  `caldav_get_week_events`, or `caldav_get_freebusy`; those are done in later sub-steps.
- Do **not** introduce a new environment variable; reuse `TZ`.
- Do not deviate from this plan.
