# Plan 01e: Validate `rrule` before adding it

## Context

This is **sub-step 01e** of the overall plan
[`01-ical-injection-escape-fix.md`](./01-ical-injection-escape-fix.md). It ensures the `rrule`
field is validated so that malformed recurrence rules are rejected rather than producing invalid
iCal.

## Current state

Before the fix, [`caldav_create_event`](../server.py:294) injected `rrule` raw:

```python
if rrule:
    ical_parts.append("RRULE:" + rrule)
```

After sub-step 01c, `rrule` is added via `event.add("rrule", rrule)`. This sub-step adds explicit
validation around that.

## Change

In [`caldav_create_event`](../server.py:254), when `rrule` is provided (truthy), validate that it
can be parsed as an RFC 5545 recurrence rule before adding it to the [`Event`](../server.py:1).

The `icalendar` library exposes `icalendar.prop.vRecur` (a wrapper around `dateutil.rrule`). Use
it to validate. If validation fails, return an error string.

Replace the current rrule handling with:

```python
if rrule:
    try:
        from icalendar.prop import vRecur
        vRecur.from_ical(rrule)
    except Exception:
        return "ERROR: invalid RRULE"
    event.add("rrule", rrule)
```

## Definition of done

- A valid RRULE string (e.g. `"FREQ=DAILY;COUNT=5"`) is added to the event.
- An invalid RRULE string (e.g. `"FREQ=BOGUS"` or `"garbage"`) is rejected with
  `"ERROR: invalid RRULE"`.
- An empty `rrule` remains a no-op.

## Constraints

- Use `icalendar.prop.vRecur` (already available via the `icalendar` dependency added in sub-step
  01a). Do **not** add a new dependency.
- Do **not** change the function signature or unrelated fields.
- Keep the validation behavior simple; do not attempt to normalize or rewrite the RRULE string.
