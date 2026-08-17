# Plan: Centralize `datetime.now` and align time semantics

## Problem

`datetime.now(...)` is called at many sites ([`server.py:69`](../server.py:69),
[`server.py:193`](../server.py:193), [`server.py:211`](../server.py:211),
[`server.py:222`](../server.py:222), [`server.py:281`](../server.py:281), etc.), each re-reading the
clock, which risks off-by-one windows around midnight and inconsistent timezones (see issue #02).

## Goal

Introduce a single `_now()` helper and compute day boundaries once per request where possible.

## Steps

1. Add a `_now()` helper returning server-timezone-aware `datetime` (ties into issue #02).
2. Add a `_start_of_day(dt)` helper to compute local midnight / day range.
3. Replace ad-hoc `datetime.now(...).replace(...)` calls with these helpers.
4. In tools that need a start/end window, compute the single `now` once and derive both bounds.
5. Add a unit test asserting day-boundary correctness around a fixed instant.

## Affected files

- `server.py` (`_parse_dt`, `caldav_get_events`, `caldav_get_today_events`,
  `caldav_get_week_events`, `caldav_get_freebusy`, `caldav_create_event`)

## Acceptance criteria

- Clock reads are centralized through `_now()`.
- Day/week windows derive from a single `now` value per request.
