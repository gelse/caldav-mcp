# Plan 11a — Extract `_validate_priority` helper

> Parent plan: [`11-priority-rrule-validation.md`](./11-priority-rrule-validation.md)

## Objective

Extract the inline `priority` validation in [`caldav_create_event`](../server.py:256) into a reusable
helper, so the validation is testable in isolation and can be folded into the issue #01 component-API
refactor.

## Context you must know

[`server.py`](../server.py) **already validates `priority`** inline at [`server.py:296`](../server.py:296):

```python
if priority:
    try:
        priority_int = int(priority)
    except (TypeError, ValueError):
        return "ERROR: priority must be an integer"
    if not 0 <= priority_int <= 9:
        return "ERROR: priority must be between 0 and 9"
    event.add("priority", priority_int)
```

The parent plan goal "validate `priority`: 0..9 (or empty)" is therefore largely satisfied. This
sub-plan extracts that logic into a pure, testable helper rather than leaving it inline, so the
round-trip/validation tests (11c) can target it directly.

The existing test file `tests/test_create_event.py` already exercises priority validation (see its
docstring and `_create` helper); do not break those assertions.

## Chosen mechanism (do not deviate)

- Add a module-level helper in [`server.py`](../server.py):

```python
def _validate_priority(priority):
    """Return (priority_int, None) on success, or (None, error_message) on failure."""
    if not priority:
        return None, None
    try:
        priority_int = int(priority)
    except (TypeError, ValueError):
        return None, "priority must be an integer"
    if not 0 <= priority_int <= 9:
        return None, "priority must be between 0 and 9"
    return priority_int, None
```

- Replace the inline block in `caldav_create_event` with a call to this helper.

## Implementation steps

### Step 1 — Add the helper

Insert `_validate_priority` near the other helpers (e.g. after `_attendee_str` at
[`server.py:172`](../server.py:172), before the first `@mcp.tool()` at line 175).

### Step 2 — Replace the inline block

In `caldav_create_event`, replace the four lines at [`server.py:296`](../server.py:296)-303 with:

```python
if priority:
    priority_int, err = _validate_priority(priority)
    if err:
        return "ERROR: " + err
    event.add("priority", priority_int)
```

### Step 3 — Verify behavior is unchanged

Run the existing tests to confirm priority validation still behaves identically:

```bash
python -m unittest discover -s tests -v
```

## Definition of done

- [ ] `_validate_priority` helper exists and preserves the exact prior behavior.
- [ ] `caldav_create_event` uses the helper; no behavior change.
- [ ] Existing tests pass unchanged.

## Constraints / rules

- Behavior-preserving refactor only; the error messages must remain byte-for-byte identical.
- Do not add new tests here (that is 11c); keep existing tests green.

## Commit

```bash
git add server.py && git commit -m "Extract _validate_priority helper"
```
