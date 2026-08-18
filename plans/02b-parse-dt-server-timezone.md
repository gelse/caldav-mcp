# Plan 02b: Update `_parse_dt` date-only policy to use the server timezone

## Context

This is **sub-step 02b** of the overall plan
[`02-timezone-utc-vs-local-fix.md`](./02-timezone-utc-vs-local-fix.md). It relies on the
`SERVER_TZ` constant introduced in [`02a-timezone-config-helpers.md`](./02a-timezone-config-helpers.md).

## Current state

[`_parse_dt`](../server.py:68) parses several ISO 8601 formats. When a parsed datetime has no
timezone info (i.e. the date-only format `%Y-%m-%d`, or a naive datetime), it currently sets
`timezone.utc` at [`server.py:84`](../server.py:84). This makes date-only inputs (e.g.
`2026-08-17`) resolve to UTC midnight, which is wrong for a server configured with a local
timezone such as `Europe/Vienna`.

## Change

All changes in this sub-step are confined to [`server.py`](../server.py), specifically the
[`_parse_dt`](../server.py:68) function.

1. Replace the naive-timezone assignment inside the `try` block of `_parse_dt`. Change the lines
   at [`server.py:83`](../server.py:83)-[`server.py:84`](../server.py:84):

   ```python
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
   ```

   to:

   ```python
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=SERVER_TZ)
            return dt
   ```

2. Also update the empty-value early return in `_parse_dt` at [`server.py:71`](../server.py:71)
   so it uses the new `_now()` helper instead of `datetime.now(timezone.utc)`:

   ```python
    if not value:
        return _now()
   ```

   (Only the function body changes; the signature and remaining format loop are preserved.)

## Definition of done

- In [`server.py`](../server.py), `_parse_dt` attaches `SERVER_TZ` (not `timezone.utc`) to naive
  parsed datetimes.
- The empty-value branch of `_parse_dt` returns `_now()`.
- With `TZ=Europe/Vienna` set, `python -c "import server; print(server._parse_dt('2026-08-17'))"`
  prints a datetime whose `tzinfo` is `Europe/Vienna` and whose wall-clock time is `00:00:00`
  (local midnight), not `00:00:00+00:00`.

## Constraints

- Do **not** change any tool functions (`caldav_get_events`, `caldav_get_today_events`,
  `caldav_get_week_events`, `caldav_get_freebusy`) in this sub-step.
- Do **not** modify files other than [`server.py`](../server.py).
- Do **not** alter the list of accepted formats or add new ones.
- Explicitly-offset inputs (e.g. ending in `Z` or `+HH:MM`) must remain unchanged in behavior.
- Do not deviate from this plan.
