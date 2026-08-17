# Plan 01d: Validate `priority` as an integer in range 0-9

## Context

This is **sub-step 01d** of the overall plan
[`01-ical-injection-escape-fix.md`](./01-ical-injection-escape-fix.md). It ensures the `priority`
field, previously injected raw into the payload, is validated and serialized as a proper integer
per RFC 5545 (an integer in the range `0..9`).

## Current state

Before the fix, [`caldav_create_event`](../server.py:292) appended `priority` raw:

```python
if priority:
    ical_parts.append("PRIORITY:" + priority)
```

After sub-step 01c, the priority handling was added defensively. This sub-step refines that into a
dedicated, clearly-defined validation.

## Change

In [`caldav_create_event`](../server.py:254), ensure `priority` is handled with explicit validation
before adding it to the [`Event`](../server.py:1). The `priority` argument is a `str` (possibly
empty).

Replace any prior priority handling with the following exact logic:

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

Place this block before the `event` is attached to `cal` (i.e. before `cal.add_component(event)`).

## Definition of done

- An empty `priority` is skipped (no `PRIORITY` property emitted).
- A non-integer `priority` (e.g. `"high"`) is rejected with `"ERROR: priority must be an integer"`.
- An out-of-range integer (e.g. `"10"` or `"-1"`) is rejected with
  `"ERROR: priority must be between 0 and 9"`.
- A valid integer `0..9` is added as an integer-typed `priority` property.

## Constraints

- Do **not** change the function signature.
- Do **not** modify unrelated fields or introduce new dependencies.
