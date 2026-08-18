# Plan 07a — Add attendee via component API in `caldav_add_attendee`

> Parent plan: [`07-attendee-string-parsing.md`](07-attendee-string-parsing.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Rewrite [`caldav_add_attendee`](server.py:370) to append an `ATTENDEE` via the parsed
`icalendar` component (a `vCalAddress` with `ROLE`/`PARTSTAT`/`RSVP` params) instead of doing raw
`.replace(...)` text surgery on `event.data`. Assumes the component access helper `_comp(event)`
is available (it is, at [`server.py:120`](server.py:120)).

## Context you must know

- Current implementation [`caldav_add_attendee`](server.py:370):
  ```python
  attendee_line = "ATTENDEE;ROLE=%s;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:%s" % (role, email)
  data = event.data
  if "END:VEVENT" in data:
      data = data.replace("END:VEVENT", attendee_line + "\r\nEND:VEVENT", 1)
  else:
      data = data + "\r\n" + attendee_line + "\r\n"
  event.data = data
  event.save()
  ```
- The `icalendar` library exposes `vCalAddress` (already imported at
  [`server.py:8`](server.py:8)) and `vText` (also imported). Component access via
  `_comp(event)` returns the `icalendar.Component` (`icalendar_component`).
- `caldav_create_event` ([`server.py:314`](server.py:314)) shows the established pattern for
  adding an attendee with the component API:
  ```python
  attendee = vCalAddress("mailto:" + email)
  attendee.params["PARTSTAT"] = vText("NEEDS-ACTION")
  attendee.params["RSVP"] = vText("TRUE")
  attendee.params["ROLE"] = vText("REQ-PARTICIPANT")
  event.add("attendee", attendee, encode=False)
  ```

## Chosen mechanism (do not deviate)

- Use the component object (via `_comp(event)`) to append the attendee, mirroring the
  `caldav_create_event` pattern, with `ROLE` set to the function's `role` parameter (default
  `"REQ-PARTICIPANT"`).
- Normalize the email: strip whitespace and prefix `mailto:` if not already present.
- Serialize the component back and persist with `event.save()` (or `event.data = ...; event.save()`
  consistent with existing code).

## Implementation steps

### Step 1 — Replace the body of `caldav_add_attendee`

Replace the text-manipulation lines with component-based logic:

```python
        event = cal.event_by_uid(uid)
        comp = _comp(event)
        if comp is None:
            return "ERROR: no icalendar component"
        email_clean = email.strip()
        if not email_clean.lower().startswith("mailto:"):
            email_clean = "mailto:" + email_clean
        attendee = vCalAddress(email_clean)
        attendee.params["PARTSTAT"] = vText("NEEDS-ACTION")
        attendee.params["RSVP"] = vText("TRUE")
        attendee.params["ROLE"] = vText(role)
        comp.add("attendee", attendee, encode=False)
        event.data = comp.to_ical().decode("utf-8")
        event.save()
        return "OK: Added attendee %s to event %s" % (email, uid)
```

### Step 2 — Preserve the outer structure

Keep the existing `try/except` (or, if 04b has landed, the typed `except AuthError` /
`except NotFoundError` / `except Exception` clauses) that surrounds the function body. Do not
rewrite those clauses in this step.

### Step 3 — Verify

Run:

```bash
python -c "import server"
python -m unittest discover -s tests -v
```

## Definition of done

- [`caldav_add_attendee`](server.py:370) no longer references `event.data` `.replace()` or
  `"END:VEVENT"` string surgery.
- The attendee is added via `vCalAddress` with `ROLE`, `PARTSTAT=NEEDS-ACTION`, `RSVP=TRUE`
  params and `encode=False`.
- Email is normalized to a `mailto:` value.
- Import and test suite pass.

## Constraints / rules

- Do NOT change `caldav_remove_attendee`, `caldav_move_event`, or any other function in this step.
- Preserve the function signature `(uid, email, calendar_name="", role="REQ-PARTICIPANT")`.
- Do not deviate from this plan.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Add attendee via icalendar component API
```
