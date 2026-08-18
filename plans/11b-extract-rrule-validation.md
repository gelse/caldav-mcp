# Plan 11b — Extract `_validate_rrule` helper

> Parent plan: [`11-priority-rrule-validation.md`](./11-priority-rrule-validation.md)

## Objective

Extract the inline `rrule` validation in [`caldav_create_event`](../server.py:256) into a reusable
helper, so malformed recurrence rules are rejected via a single, testable path.

## Context you must know

[`server.py`](../server.py) **already validates `rrule`** inline at [`server.py:305`](../server.py:305):

```python
if rrule:
    try:
        from icalendar.prop import vRecur

        vRecur.from_ical(rrule)
    except Exception:
        return "ERROR: invalid RRULE"
    event.add("rrule", rrule)
```

The parent plan goal "validate `rrule` using the `icalendar` recurrence parser before adding" is
satisfied, but the `from icalendar.prop import vRecur` should be lifted to a top-level import and the
logic extracted into a helper for testability. Note the current `except Exception` is intentionally
broad (matching the pre-plan-04 style) — preserve the error string exactly.

## Chosen mechanism (do not deviate)

- Add a top-level import at [`server.py:1`](../server.py:1) region:

```python
from icalendar.prop import vRecur
```

- Add a module-level helper:

```python
def _validate_rrule(rrule):
    """Return True if rrule is empty/valid, False otherwise."""
    if not rrule:
        return True
    try:
        vRecur.from_ical(rrule)
    except Exception:
        return False
    return True
```

- Replace the inline block in `caldav_create_event` with a call to the helper.

## Implementation steps

### Step 1 — Lift the import

Add `from icalendar.prop import vRecur` to the top of [`server.py`](../server.py) alongside the other
`icalendar` imports.

### Step 2 — Add the helper

Insert `_validate_rrule` near `_validate_priority` (from 11a) before the first `@mcp.tool()`.

### Step 3 — Replace the inline block

Replace the block at [`server.py:305`](../server.py:305)-312 with:

```python
if rrule:
    if not _validate_rrule(rrule):
        return "ERROR: invalid RRULE"
    event.add("rrule", rrule)
```

### Step 4 — Verify behavior is unchanged

```bash
python -m unittest discover -s tests -v
```

## Definition of done

- [ ] `_validate_rrule` helper exists; `vRecur` import is at module top level.
- [ ] `caldav_create_event` uses the helper; error string unchanged.
- [ ] Existing tests pass unchanged.

## Constraints / rules

- Keep the error message `invalid RRULE` byte-for-byte identical.
- Behavior-preserving refactor only; no new tests here.

## Commit

```bash
git add server.py && git commit -m "Extract _validate_rrule helper"
```
