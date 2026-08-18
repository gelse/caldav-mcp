# Plan 07b — Remove attendee via component API in `caldav_remove_attendee`

> Parent plan: [`07-attendee-string-parsing.md`](07-attendee-string-parsing.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Rewrite [`caldav_remove_attendee`](server.py:391) to filter the component's `ATTENDEE` list by a
normalized value instead of doing raw `splitlines()`/`startswith("ATTENDEE")` text matching.
Assumes `_comp(event)` is available ([`server.py:120`](server.py:120)).

## Context you must know

- Current implementation [`caldav_remove_attendee`](server.py:391):
  ```python
  target = "mailto:" + email
  data = event.data
  if target not in data:
      return "Attendee %s not found on event %s" % (email, uid)
  new_lines = []
  for line in data.splitlines():
      ul = line.upper()
      if ul.startswith("ATTENDEE") and target in line:
          continue
      new_lines.append(line)
  event.data = "\r\n".join(new_lines)
  event.save()
  ```
- The `icalendar` component exposes `comp.get("attendee")` which returns a single `vCalAddress`
  or a list/tuple of them. Each `vCalAddress` stringifies as `mailto:user@host`.
- `vCalAddress` (and `vText`) are imported at [`server.py:8`](server.py:8).

## Chosen mechanism (do not deviate)

- Normalize the target email: strip whitespace, lowercase the local mailbox for comparison, and
  prefix `mailto:` if absent.
- Read `comp.get("attendee")`, normalize to a list, filter out entries whose normalized value
  equals the target, then write the remaining attendees back to the component and persist.

## Implementation steps

### Step 1 — Replace the body of `caldav_remove_attendee`

Replace the text-manipulation logic with component-based filtering:

```python
        event = cal.event_by_uid(uid)
        comp = _comp(event)
        if comp is None:
            return "ERROR: no icalendar component"
        target = email.strip()
        if not target.lower().startswith("mailto:"):
            target = "mailto:" + target
        target_norm = target.lower()

        current = comp.get("attendee")
        if current is None:
            return "Attendee %s not found on event %s" % (email, uid)
        if not isinstance(current, (list, tuple)):
            current = [current]

        remaining = [a for a in current if str(a).strip().lower() != target_norm]
        if len(remaining) == len(current):
            return "Attendee %s not found on event %s" % (email, uid)

        if remaining:
            comp["attendee"] = remaining
        else:
            del comp["attendee"]
        event.data = comp.to_ical().decode("utf-8")
        event.save()
        return "OK: Removed attendee %s from event %s" % (email, uid)
```

Note: if `del comp["attendee"]` is not supported for that key type, use `comp.pop("attendee")`.
Choose one and be consistent.

### Step 2 — Preserve the outer structure

Keep the existing `try/except` (or typed `except` clauses from 04b) around the body. Do not
rewrite them here.

### Step 3 — Verify

Run:

```bash
python -c "import server"
python -m unittest discover -s tests -v
```

## Definition of done

- [`caldav_remove_attendee`](server.py:391) no longer uses `splitlines()` or
  `startswith("ATTENDEE")` text matching on `event.data`.
- Removal filters attendees by normalized value and handles the not-found case.
- Serialization is done via `comp.to_ical()` and persisted with `event.save()`.
- Import and test suite pass.

## Constraints / rules

- Do NOT change `caldav_add_attendee`, `caldav_move_event`, or any other function in this step.
- Preserve the function signature `(uid, email, calendar_name="")`.
- Do not deviate from this plan.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Remove attendee via icalendar component API
```
