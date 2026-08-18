# Plan 02f: Add timezone unit tests and verify/finalize

## Context

This is **sub-step 02f** (final) of the overall plan
[`02-timezone-utc-vs-local-fix.md`](./02-timezone-utc-vs-local-fix.md). It adds unit tests covering
the timezone behavior introduced in sub-steps 02a-02d, then verifies the full suite passes. It
relies on all prior sub-steps being complete.

## Change

### 1. Add `tests/test_timezone.py`

Create a new file [`tests/test_timezone.py`](../tests/test_timezone.py) using the standard-library
`unittest` framework (matching the existing [`tests/test_create_event.py`](../tests/test_create_event.py)
style: `import unittest`, `import server`). The tests must exercise only pure, network-free
functions and helpers so they run without a live CalDAV server.

The file must cover, at minimum:

1. **`_server_tz()` / `SERVER_TZ` resolution** — using `unittest.mock.patch.dict` on
   `os.environ` (or `mock.patch.object(server.os, "environ", ...)`):

   - With `TZ` set to `"Europe/Vienna"`, `server._server_tz()` returns a `ZoneInfo` with key
     `"Europe/Vienna"` (assert its DST offset is non-zero in summer, or simply assert
     `str(tz) == "Europe/Vienna"`).
   - With `TZ` unset/empty, `server._server_tz()` returns `datetime.timezone.utc`.
   - With `TZ` set to an invalid name (e.g. `"Not/AZone"`), `server._server_tz()` falls back to
     `datetime.timezone.utc` and does not raise.

   Note: `SERVER_TZ` is computed once at import; for these tests, call `_server_tz()` directly to
   observe the env-dependent behavior rather than relying on the cached module constant.

2. **`_now()` / `_start_of_day()`** — assert that `_now()` carries the server timezone (or UTC
   when unset), and that `_start_of_day(dt)` preserves the timezone while zeroing
   hour/minute/second/microsecond:

   ```python
   dt = datetime(2026, 8, 17, 15, 30, 45, 123456, tzinfo=ZoneInfo("Europe/Vienna"))
   sd = _start_of_day(dt)
   # assert sd == datetime(2026, 8, 17, 0, 0, 0, 0, tzinfo=ZoneInfo("Europe/Vienna"))
   ```

3. **`_parse_dt` date-only and naive inputs** — with `TZ=Europe/Vienna` patched:

   - `_parse_dt("2026-08-17")` returns a datetime whose `tzinfo` is `Europe/Vienna` and whose
     hour/minute/second/microsecond are all `0` (local midnight).
   - A naive datetime string (`"2026-08-17 10:00:00"`) also resolves to `Europe/Vienna`.
   - An explicit `"Z"`-suffixed input (`"2026-01-01T10:00:00Z"`) still returns a UTC-aware
     datetime (behavior unchanged).
   - `_parse_dt("")` returns `_now()`.

   Use `mock.patch.dict(os.environ, {"TZ": "Europe/Vienna"})` and, because `_server_tz` reads the
   env at call time, call the functions inside that context. For highest fidelity, prefer testing
   `_server_tz()` and `_parse_dt` while the env is patched — `_parse_dt` reads the module-level
   `SERVER_TZ`, so also patch `server.SERVER_TZ` to the resolved value when testing `_parse_dt`,
   or structure the test to set `TZ` before importing/using the patched constant.

   To keep tests deterministic and independent of import-time env, prefer patching the module
   constant directly:

   ```python
   with mock.patch.object(server, "SERVER_TZ", ZoneInfo("Europe/Vienna")):
       # assertions on _parse_dt / _now / _start_of_day default behavior
   ```

   Choose the combination of `mock.patch.dict` (for `_server_tz`) and `mock.patch.object` (for
   `SERVER_TZ`) that is simplest and still meaningful; both are acceptable.

4. **(Optional but recommended) `_start_of_day` used by helpers** — where straightforward,
   verify `caldav_get_today_events` / `caldav_get_week_events` pass local-midnight ISO strings by
   mocking `caldav_get_events` and asserting the `start` argument resolves to local midnight.
   Keep this optional to avoid coupling to network stubs.

Each test must be a method on a `unittest.TestCase` subclass, and the file must end with:

```python
if __name__ == "__main__":
    unittest.main()
```

### 2. Verify

Run the full test suite from the workspace root and confirm it passes:

```bash
python -m unittest discover -s tests -v
```

Also run a quick import sanity check:

```bash
TZ=Europe/Vienna python -c "import server; print(server._server_tz()); print(server._parse_dt('2026-08-17'))"
```

Confirm the output shows `Europe/Vienna` and a local-midnight datetime.

## Definition of done

- [`tests/test_timezone.py`](../tests/test_timezone.py) exists and passes.
- The full existing suite (`tests/test_create_event.py` + new test) passes via
  `python -m unittest discover -s tests -v`.
- No regressions: existing `caldav_create_event` tests still pass.

## Constraints

- Use only the standard library (`unittest`, `unittest.mock`, `datetime`, `zoneinfo`); do not add
  new dependencies.
- Tests must be deterministic and must not require a live CalDAV server or network access.
- Do **not** modify [`server.py`](../server.py) in this sub-step (all code changes were done in
  02a-02d).
- Do not deviate from this plan; only implement what is specified here.
