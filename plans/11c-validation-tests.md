# Plan 11c — Add priority and rrule validation tests

> Parent plan: [`11-priority-rrule-validation.md`](./11-priority-rrule-validation.md)

## Objective

Add unit tests for valid and invalid `priority` and `rrule` values, targeting the extracted helpers
(11a/11b) and confirming `caldav_create_event` rejects bad input with clear errors.

## Context you must know

The existing [`tests/test_create_event.py`](../tests/test_create_event.py) already covers some
priority/rrule validation through `caldav_create_event`. This sub-plan adds focused, isolated tests
for the new helpers `_validate_priority` (11a) and `_validate_rrule` (11b), plus end-to-end rejection
assertions.

Helper contracts:

- `_validate_priority(priority)` → `(int|None, str|None)`; error strings are `"priority must be an integer"`
  and `"priority must be between 0 and 9"`.
- `_validate_rrule(rrule)` → `bool` (True = empty or valid).

## Chosen mechanism (do not deviate)

- Create `tests/test_validation.py` using `unittest.TestCase`, importing helpers from `server`.
- Reuse the fake-network pattern from `tests/test_create_event.py` only where you need to assert
  `caldav_create_event` returns the error strings.

## Implementation steps

### Step 1 — Create `tests/test_validation.py`

```python
import unittest
import server


class ValidatePriorityTest(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertEqual(server._validate_priority(""), (None, None))

    def test_valid(self):
        self.assertEqual(server._validate_priority("0"), (0, None))
        self.assertEqual(server._validate_priority("9"), (9, None))

    def test_non_integer(self):
        _, err = server._validate_priority("abc")
        self.assertEqual(err, "priority must be an integer")

    def test_out_of_range(self):
        _, err = server._validate_priority("10")
        self.assertEqual(err, "priority must be between 0 and 9")
        _, err = server._validate_priority("-1")
        self.assertEqual(err, "priority must be between 0 and 9")


class ValidateRruleTest(unittest.TestCase):
    def test_empty_is_true(self):
        self.assertTrue(server._validate_rrule(""))

    def test_valid_daily(self):
        self.assertTrue(server._validate_rrule("FREQ=DAILY"))

    def test_invalid(self):
        self.assertFalse(server._validate_rrule("NOT-A-RRULE;;"))
```

### Step 2 — Add end-to-end rejection tests (optional)

If you want create-task coverage, mirror `test_create_event.py`'s `patch_network`/`_create` helpers and
assert `caldav_create_event(priority="11", ...)` and `caldav_create_event(rrule="bad", ...)` return the
expected `ERROR:` strings.

### Step 3 — Run tests

```bash
python -m unittest discover -s tests -v
pytest -q
```

## Definition of done

- [ ] `tests/test_validation.py` exists.
- [ ] Valid and invalid `priority` (non-integer, out of range) are covered.
- [ ] Valid and invalid `rrule` are covered.
- [ ] Full suite passes via `unittest` and `pytest`.

## Constraints / rules

- Assert against the **actual** helper return shapes and error strings; do not invent values.
- Do not modify [`server.py`](../server.py).

## Commit

```bash
git add tests/test_validation.py && git commit -m "Add priority and rrule validation tests"
```
