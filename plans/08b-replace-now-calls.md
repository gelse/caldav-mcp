# Plan 08b — Replace ad-hoc `datetime.now` calls with helpers

> Parent plan: [`08-datetime-now-consistency.md`](08-datetime-now-consistency.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Replace every ad-hoc `datetime.now(...)` / `.replace(...)` clock read in the tools with the
`_now()` and `_start_of_day(dt)` helpers, and compute day/week windows from a single `now` value
per request. Assumes 08a is complete.

## Context you must know

- Helpers `_now()` and `_start_of_day(dt)` are now available (from 08a).
- Current call sites and the intended replacement:

  1. [`_parse_dt`](server.py:71): `return datetime.now(timezone.utc)` when value is empty →
     `return _now()`.
  2. [`caldav_get_events`](server.py:195):
     ```python
     start_dt = _parse_dt(start) if start else datetime.now(timezone.utc).replace(
         hour=0, minute=0, second=0, microsecond=0
     )
     end_dt = _parse_dt(end) if end else (start_dt + timedelta(days=1))
     ```
     → compute `now = _now()` once, then `start_dt = _parse_dt(start) if start else
     _start_of_day(now)`, `end_dt = _parse_dt(end) if end else (start_dt + timedelta(days=1))`.
  3. [`caldav_get_today_events`](server.py:213):
     ```python
     today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
     ```
     → `today = _start_of_day(_now())`.
  4. [`caldav_get_week_events`](server.py:224):
     ```python
     now = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
     ```
     → `now = _start_of_day(_now())`.
  5. [`caldav_create_event`](server.py:284): `event.add("dtstamp", datetime.now(timezone.utc))`
     → `event.add("dtstamp", _now())`.
  6. [`caldav_get_freebusy`](server.py:493): same pattern as `caldav_get_events` →
     single `now = _now()`, `_start_of_day(now)`.

## Implementation steps

### Step 1 — Apply replacements

Apply the replacements above, one call site at a time. For range-based tools
(`caldav_get_events`, `caldav_get_freebusy`), read `_now()` once and derive both bounds so there
is no double clock read.

### Step 2 — Verify no stray clock reads

Search for remaining `datetime.now(` in [`server.py`](server.py). The only legitimate remaining
uses should be none in tool bodies (helpers centralize them). If a `datetime.now` remains, ensure
it is intentional (e.g. inside `_now` itself) and not a missed call site.

### Step 3 — Verify

Run:

```bash
python -c "import server"
python -m unittest discover -s tests -v
```

## Definition of done

- No tool body reads `datetime.now(...)` directly; all go through `_now()`.
- Day/week windows in `caldav_get_events`, `caldav_get_today_events`,
  `caldav_get_week_events`, and `caldav_get_freebusy` derive from a single `_now()` value.
- `caldav_create_event` uses `_now()` for `dtstamp`.
- Import and test suite pass.

## Constraints / rules

- Do NOT change the `_parse_dt` accepted formats or timezone semantics beyond substituting
  `_now()` for the empty-value default.
- Do NOT alter function signatures.
- Do not deviate from this plan.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Centralize datetime.now through helpers
```
