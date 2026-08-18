# Plan 02c: Update `caldav_get_events` default start/end boundaries

## Context

This is **sub-step 02c** of the overall plan
[`02-timezone-utc-vs-local-fix.md`](./02-timezone-utc-vs-local-fix.md). It relies on:
- [`02a-timezone-config-helpers.md`](./02a-timezone-config-helpers.md) (`SERVER_TZ`, `_now`,
  `_start_of_day`).
- [`02b-parse-dt-server-timezone.md`](./02b-parse-dt-server-timezone.md) (naive inputs resolve to
  server timezone).

## Current state

[`caldav_get_events`](../server.py:189) computes its default `start_dt` using
`datetime.now(timezone.utc)` and then zeroes the time fields at
[`server.py:195`](../server.py:195)-[`server.py:197`](../server.py:197). This produces UTC midnight
as the default start boundary, which is off by the local offset for a server configured with a
local timezone.

## Change

All changes in this sub-step are confined to [`server.py`](../server.py), specifically the
[`caldav_get_events`](../server.py:189) function.

1. Replace the default `start_dt` computation. Change the lines at
   [`server.py:195`](../server.py:195)-[`server.py:197`](../server.py:197):

   ```python
        start_dt = _parse_dt(start) if start else datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
   ```

   to:

   ```python
        start_dt = _parse_dt(start) if start else _start_of_day(_now())
   ```

2. Leave the `end_dt` fallback logic unchanged at [`server.py:198`](../server.py:198)
   (`end_dt = _parse_dt(end) if end else (start_dt + timedelta(days=1))`). It already derives
   correctly from the new `start_dt`.

## Definition of done

- In [`server.py`](../server.py), `caldav_get_events` computes its default `start_dt` via
  `_start_of_day(_now())` (server-timezone local midnight), not via `datetime.now(timezone.utc)`.
- With `TZ=Europe/Vienna`, the default start boundary is `00:00:00+02:00`-equivalent (local
  midnight), and the default end is exactly 24 hours later.

## Constraints

- Do **not** modify [`caldav_get_today_events`](../server.py:211),
  [`caldav_get_week_events`](../server.py:222), or [`caldav_get_freebusy`](../server.py:487) in this
  sub-step; those are covered by [`02d`](./02d-today-week-freebusy.md).
- Do **not** modify files other than [`server.py`](../server.py).
- Preserve the existing error-handling `try/except` wrapper and return-string behavior.
- Do not deviate from this plan.
