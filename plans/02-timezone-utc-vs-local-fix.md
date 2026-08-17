# Plan: Fix UTC vs local timezone handling (TZ ignored)

## Problem

The server hardcodes `timezone.utc` everywhere (e.g. [`_parse_dt`](../server.py:66), the
"today"/"week" helpers at [`server.py:211`](../server.py:211) and [`server.py:222`](../server.py:222)).
The container sets `TZ: Europe/Vienna` in [`docker-compose.yaml`](../docker-compose.yaml:14), but
that value is ignored. Consequently "today" events are computed against UTC midnight, which is
off by the local offset (e.g. Vienna is UTC+2 in summer), and date-only inputs like `2026-08-17`
are treated as UTC midnight.

## Goal

Make timezone handling explicit and correct, so "today"/"week" boundaries and date-only inputs
align with the intended timezone.

## Steps

1. Read a `CALDAV_MCP_TZ` (or reuse `TZ`) env var to determine the server timezone, defaulting to
   `UTC`.
2. Introduce a helper `_now()` returning `datetime.now(server_tz)` and a `_start_of_day()` to
   compute local day boundaries.
3. Update `caldav_get_events` default start/end, `caldav_get_today_events`, `caldav_get_week_events`,
   and `caldav_get_freebusy` to use the server timezone instead of `timezone.utc`.
4. Decide the policy for date-only `_parse_dt` inputs: attach the server timezone rather than UTC.
5. Document the new timezone env var in [`README.md`](../README.md:21) and
   [`.env.example`](../.env.example).

## Affected files

- `server.py` (`_parse_dt`, get_events/today/week/freebusy helpers)
- `README.md`, `.env.example`, `docker-compose.yaml`

## Acceptance criteria

- "today" boundaries correspond to the configured timezone, not UTC.
- Date-only inputs resolve to the configured timezone.
- New behavior is documented.
