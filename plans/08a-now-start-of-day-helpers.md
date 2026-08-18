# Plan 08a — Add `_now()` and `_start_of_day()` helpers

> Parent plan: [`08-datetime-now-consistency.md`](08-datetime-now-consistency.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Introduce two time helpers — `_now()` (server-timezone-aware current datetime) and
`_start_of_day(dt)` (local midnight) — to centralize the ad-hoc `datetime.now(...)` calls that
currently repeat across [`server.py`](server.py). This step only adds the helpers; wiring is done
in 08b.

## Context you must know

- [`server.py:4`](server.py:4) currently imports:
  ```python
  from datetime import datetime, timedelta, timezone
  ```
- The parent plan's goal connects to issue #02 (timezone handling). If plan 02's `_server_tz()`
  / `SERVER_TZ` helpers already exist, reuse them; if not, add a minimal timezone resolution.
- Current ad-hoc sites all use `datetime.now(timezone.utc)`, e.g.
  [`_parse_dt`](server.py:71), [`caldav_get_events`](server.py:195),
  [`caldav_get_today_events`](server.py:213), [`caldav_get_week_events`](server.py:224),
  [`caldav_create_event`](server.py:284), [`caldav_get_freebusy`](server.py:493).

## Chosen mechanism (do not deviate)

- `_now()` returns `datetime.now(<server-tz>)`. Use the server timezone that other plans (02)
  establish; for this step, use a helper `SERVER_TZ` if present, otherwise default to
  `timezone.utc`.
- `_start_of_day(dt)` returns `dt.replace(hour=0, minute=0, second=0, microsecond=0)`,
  preserving `tzinfo`.

## Implementation steps

### Step 1 — Add server timezone resolution (only if absent)

If no `SERVER_TZ`/`_server_tz()` exists yet, add near the other module constants:

```python
SERVER_TZ = timezone.utc  # placeholder; see plan 02 for real resolution
```

(If plan 02 already defined `SERVER_TZ`, reuse that symbol and skip this placeholder.)

### Step 2 — Add `_now()`

Place near [`_parse_dt`](server.py:68):

```python
def _now():
    return datetime.now(SERVER_TZ)
```

### Step 3 — Add `_start_of_day(dt)`

```python
def _start_of_day(dt):
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)
```

### Step 4 — Verify

Run:

```bash
python -c "import server; print(server._now(), server._start_of_day(server._now()))"
```

## Definition of done

- `_now()` and `_start_of_day(dt)` exist in [`server.py`](server.py).
- `_now()` is timezone-aware (uses `SERVER_TZ`).
- `_start_of_day(dt)` zeroes hour/minute/second/microsecond and preserves tzinfo.
- No existing call site is modified yet.

## Constraints / rules

- Do NOT replace any `datetime.now(...)` call sites in this step (that is 08b).
- Reuse the existing `SERVER_TZ`/`_server_tz()` if plan 02 has landed; do not duplicate.
- Do not deviate from this plan.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Add _now and _start_of_day helpers
```
