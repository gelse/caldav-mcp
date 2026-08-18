# Plan 10b — Add `_parse_dt` unit tests

> Parent plan: [`10-tests-linting-typing.md`](./10-tests-linting-typing.md)

## Objective

Add unit tests covering the pure `_parse_dt` helper in [`server.py`](../server.py) for all accepted
formats, the `Z` suffix, date-only input, invalid input, and timezone handling.

## Context you must know

`_parse_dt(value)` is defined at [`server.py:68`](../server.py:68). It returns a timezone-aware
`datetime`. Its accepted formats are:

- `%Y-%m-%dT%H:%M:%S%z` (e.g. `2026-01-01T10:00:00+0100`)
- `%Y-%m-%dT%H:%M%z`
- `%Y-%m-%d %H:%M:%S%z`
- `%Y-%m-%d %H:%M%z`
- `%Y-%m-%d` (date only)

Empty/whitespace input returns `datetime.now(timezone.utc)`. (Depending on the state of plan 02, the
date-only branch and the "now" fallback may consult a server timezone — re-read the current
[`server.py`](../server.py) implementation and assert against **actual** behavior, not this summary.)

The existing test file [`tests/test_create_event.py`](../tests/test_create_event.py) uses
`unittest.TestCase` and imports nothing from the server today; a new test file is preferred here to
keep concerns separate.

## Chosen mechanism (do not deviate)

- Create a new file `tests/test_parse_dt.py` using `unittest.TestCase`.
- Import `_parse_dt` from `server` (add `import server` and call `server._parse_dt(...)`, or
  `from server import _parse_dt`).
- Use `datetime` and `timezone` from the stdlib for expected values.

## Implementation steps

### Step 1 — Create the test file

Create `tests/test_parse_dt.py` with a `unittest.TestCase` class `ParseDtTest`.

### Step 2 — Add format tests

For each accepted format, assert that `_parse_dt` returns an aware `datetime` equal to the expected
value. Example:

```python
from datetime import datetime, timedelta, timezone

class ParseDtTest(unittest.TestCase):
    def test_full_seconds_with_offset(self):
        got = server._parse_dt("2026-01-01T10:00:00+0100")
        self.assertEqual(got, datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone(timedelta(hours=1))))
```

### Step 3 — Add `Z` suffix test

If the implementation normalizes `Z` (e.g. replaces it with `+0000`), assert the correct UTC result;
otherwise assert the actual documented behavior. (Confirm against [`server.py:68`](../server.py:68)
first.)

### Step 4 — Add date-only and empty-input tests

- Date-only `2026-01-01` → assert the resulting datetime (timezone-aware per implementation).
- Empty string `""` → assert result is close to `datetime.now(timezone.utc)` (e.g. within a small
  delta), or the exact fallback the code implements.

### Step 5 — Add invalid-input test

For a clearly invalid string (e.g. `"not-a-date"`), assert that `_parse_dt` raises (or logs and
falls back) exactly as implemented. Do not invent behavior — read [`server.py:68`](../server.py:68)
and assert the real outcome.

### Step 6 — Run tests

```bash
python -m unittest discover -s tests -v
pytest -q
```

## Definition of done

- [ ] `tests/test_parse_dt.py` exists with a `unittest.TestCase`.
- [ ] All accepted formats, `Z` suffix, date-only, empty input, and invalid input are covered.
- [ ] Assertions match the actual `_parse_dt` behavior (no fabricated expectations).
- [ ] Full suite passes via both `unittest` and `pytest`.

## Constraints / rules

- Do not modify [`server.py`](../server.py). If tests expose a bug, note it in the attempt_completion
  result but do not fix it here.
- New test file only; leave existing tests untouched.

## Commit

```bash
git add tests/test_parse_dt.py && git commit -m "Add _parse_dt unit tests"
```
