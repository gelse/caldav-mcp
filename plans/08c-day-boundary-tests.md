# Plan 08c — Add day-boundary tests and verify

> Parent plan: [`08-datetime-now-consistency.md`](08-datetime-now-consistency.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Add a unit test asserting day-boundary correctness around a fixed instant for `_now()` /
`_start_of_day()`, then run the full suite. Assumes 08a and 08b are complete.

## Context you must know

- Helpers `_now()` and `_start_of_day(dt)` exist in [`server.py`](server.py).
- Existing tests use stdlib `unittest` / `unittest.mock` and are network-free.

## Implementation steps

### Step 1 — Create `tests/test_datetime_helpers.py`

Create [`tests/test_datetime_helpers.py`](tests/test_datetime_helpers.py) with a
`unittest.TestCase` subclass. Use `mock.patch.object(server, "SERVER_TZ", ...)` (or patch the
timezone) to make `_now()` deterministic if needed.

Cover, at minimum:

1. **`_start_of_day` preserves tzinfo and zeroes time** — given
   `dt = datetime(2026, 8, 17, 15, 30, 45, 123456, tzinfo=timezone.utc)`, assert
   `server._start_of_day(dt) == datetime(2026, 8, 17, 0, 0, 0, 0, tzinfo=timezone.utc)`.
2. **`_now` is timezone-aware** — assert `server._now().tzinfo is not None`.
3. **Day boundary correctness around a fixed instant** — with a fixed mocked "now", assert that
   `_start_of_day(_now())` plus `timedelta(days=1)` produces a contiguous 24-hour window
   (start hour/minute/second all zero, end equals start + 1 day).

Each test method ends with the file-level guard:

```python
if __name__ == "__main__":
    unittest.main()
```

### Step 2 — Verify

Run:

```bash
python -m unittest discover -s tests -v
```

## Definition of done

- [`tests/test_datetime_helpers.py`](tests/test_datetime_helpers.py) exists and passes.
- Tests cover tz-aware `_now`, `_start_of_day` zeroing, and a fixed-instant day boundary.
- Full suite passes.

## Constraints / rules

- Use only stdlib (`unittest`, `unittest.mock`, `datetime`); no new dependencies.
- Deterministic, no live network.
- Do NOT modify [`server.py`](server.py) in this step.
- Do not deviate from this plan.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Add datetime helper unit tests
```
