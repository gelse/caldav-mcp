# Plan 07c — Move event via component UID in `caldav_move_event`

> Parent plan: [`07-attendee-string-parsing.md`](07-attendee-string-parsing.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Rewrite [`caldav_move_event`](server.py:446) to set a new `UID` on the parsed component and
serialize it, instead of `.replace("UID:" + uid, "UID:" + new_uid, 1)` text surgery. Assumes
`_comp(event)` is available ([`server.py:120`](server.py:120)).

## Context you must know

- Current implementation [`caldav_move_event`](server.py:446):
  ```python
  event = src_cal.event_by_uid(uid)
  data = event.data
  new_uid = "%s@caldav-mcp" % uuid.uuid4()
  data = data.replace("UID:" + uid, "UID:" + new_uid, 1)
  dst_cal.save_event(data)
  event.delete()
  return "OK: Moved event %s -> %s (new uid=%s)" % (uid, target_calendar, new_uid)
  ```
- The component supports `comp["UID"] = new_uid` (or `comp.add("uid", new_uid)`).
- `uuid` is imported at [`server.py:2`](server.py:2).

## Chosen mechanism (do not deviate)

- Get the component via `_comp(event)`; if `None`, return an error.
- Set `comp["UID"] = new_uid` (preserving existing component case conventions; the iCal key is
  `UID`).
- Serialize with `comp.to_ical().decode("utf-8")` and save to the destination calendar with
  `dst_cal.save_event(serialized)`.

## Implementation steps

### Step 1 — Replace the body of `caldav_move_event`

Replace the `.replace(...)` logic:

```python
        event = src_cal.event_by_uid(uid)
        comp = _comp(event)
        if comp is None:
            return "ERROR: no icalendar component"
        new_uid = "%s@caldav-mcp" % uuid.uuid4()
        comp["UID"] = new_uid
        dst_cal.save_event(comp.to_ical().decode("utf-8"))
        event.delete()
        return "OK: Moved event %s -> %s (new uid=%s)" % (uid, target_calendar, new_uid)
```

(If the destination calendar component key must be `"uid"` lowercase rather than `"UID"`, use
whatever the existing `_event_to_dict`/`_text` helpers read via `comp.get("uid")` — verify by
checking [`_event_to_dict`](server.py:147) reads `comp.get("uid")`. Match the key the library
uses; test in the verification step.)

### Step 2 — Preserve the outer structure

Keep the existing `try/except` (or typed `except` clauses from 04b) around the body.

### Step 3 — Verify

Run:

```bash
python -c "import server"
python -m unittest discover -s tests -v
```

## Definition of done

- [`caldav_move_event`](server.py:446) no longer uses `.replace("UID:...")` on `event.data`.
- The new UID is set on the component and serialized via `comp.to_ical()`.
- The destination receives `dst_cal.save_event(serialized)` and the original is `event.delete()`d.
- Import and test suite pass.

## Constraints / rules

- Do NOT change `caldav_add_attendee`, `caldav_remove_attendee`, or any other function here.
- Preserve the signature `(uid, target_calendar, source_calendar="")`.
- Do not deviate from this plan.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Move event via component UID update
```
