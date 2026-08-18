# Plan 02d: Update `caldav_get_today_events`, `caldav_get_week_events`, and `caldav_get_freebusy`

## Context

This is **sub-step 02d** of the overall plan
[`02-timezone-utc-vs-local-fix.md`](./02-timezone-utc-vs-local-fix.md). It relies on the
`SERVER_TZ`, `_now`, and `_start_of_day` helpers introduced in
[`02a-timezone-config-helpers.md`](./02a-timezone-config-helpers.md), and on
[`02b-parse-dt-server-timezone.md`](./02b-parse-dt-server-timezone.md).

## Current state

Three helpers still hardcode `datetime.now(timezone.utc).replace(...)` to compute day boundaries:

- [`caldav_get_today_events`](../server.py:211) at [`server.py:213`](../server.py:213).
- [`caldav_get_week_events`](../server.py:222) at [`server.py:224`](../server.py:224).
- [`caldav_get_freebusy`](../server.py:487) at [`server.py:493`](../server.py:493)-[`server.py:495`](../server.py:495).

## Change

All changes in this sub-step are confined to [`server.py`](../server.py).

### 1. `caldav_get_today_events`

Replace the line at [`server.py:213`](../server.py:213):

```python
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
```

with:

```python
    today = _start_of_day(_now())
```

Leave the rest of the function (the call to `caldav_get_events` with `start=today.isoformat()` and
`end=(today + timedelta(days=1)).isoformat()`) unchanged.

### 2. `caldav_get_week_events`

Replace the line at [`server.py:224`](../server.py:224):

```python
    now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
```

with:

```python
    now = _start_of_day(_now())
```

Leave the rest of the function unchanged.

### 3. `caldav_get_freebusy`

Replace the lines at [`server.py:493`](../server.py:493)-[`server.py:495`](../server.py:495):

```python
        start_dt = _parse_dt(start) if start else datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
```

with:

```python
        start_dt = _parse_dt(start) if start else _start_of_day(_now())
```

Leave the `end_dt` fallback line at [`server.py:496`](../server.py:496) unchanged.

## Definition of done

- In [`server.py`](../server.py), none of `caldav_get_today_events`, `caldav_get_week_events`, or
  `caldav_get_freebusy` references `datetime.now(timezone.utc)` anymore.
- All three compute day boundaries via `_start_of_day(_now())` in the server timezone.
- With `TZ=Europe/Vienna`, `caldav_get_today_events` passes a `start` of local midnight (not UTC
  midnight) into `caldav_get_events`, and `caldav_get_week_events` covers exactly the next 7 local
  days.

## Constraints

- Do **not** modify files other than [`server.py`](../server.py).
- Do **not** change the function signatures, return strings, or error handling.
- Do not deviate from this plan.
