# Plan 01c: Replace string-built VEVENT payload with `icalendar` component

## Context

This is **sub-step 01c** of the overall plan
[`01-ical-injection-escape-fix.md`](./01-ical-injection-escape-fix.md). This is the core change:
replace the naive `ical_parts` string list in [`caldav_create_event`](../server.py:275) with a
properly-constructed [`Calendar`](../server.py:1)/[`Event`](../server.py:1) component.

## Current state

[`caldav_create_event`](../server.py:254) currently builds the payload as raw strings:

```python
uid = "%s@caldav-mcp" % uuid.uuid4()
ical_parts = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//caldav-mcp//EN",
    "BEGIN:VEVENT",
    "UID:" + uid,
    "DTSTAMP:" + _format_ical_dt(datetime.now(timezone.utc)),
    "DTSTART:" + _format_ical_dt(start_dt),
    "DTEND:" + _format_ical_dt(end_dt),
    "SUMMARY:" + summary,
]
if location:
    ical_parts.append("LOCATION:" + location)
if description:
    ical_parts.append("DESCRIPTION:" + description)
if categories:
    ical_parts.append("CATEGORIES:" + categories)
if priority:
    ical_parts.append("PRIORITY:" + priority)
if rrule:
    ical_parts.append("RRULE:" + rrule)
if attendees:
    for email in attendees.split(","):
        email = email.strip()
        if email:
            ical_parts.append("ATTENDEE;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:" + email)
ical_parts.extend(["END:VEVENT", "END:VCALENDAR"])

cal.save_event("\r\n".join(ical_parts) + "\r\n")
```

## Change

Replace the body of [`caldav_create_event`](../server.py:274) (from the `uid = ...` line through
the `cal.save_event(...)` line) with `icalendar` component construction.

### 1. Build the calendar and event

```python
uid = "%s@caldav-mcp" % uuid.uuid4()

cal = Calendar()
cal.add("prodid", "-//caldav-mcp//EN")
cal.add("version", "2.0")

event = Event()
event.add("uid", uid)
event.add("dtstamp", datetime.now(timezone.utc))
event.add("dtstart", start_dt)
event.add("dtend", end_dt)
event.add("summary", summary)
```

### 2. Add optional single-valued text properties

Add each key only when its value is truthy:

```python
if location:
    event.add("location", location)
if description:
    event.add("description", description)
if categories:
    event.add("categories", categories)
if rrule:
    event.add("rrule", rrule)
```

Add `priority` only if it is a valid integer in the range `0..9` (see sub-step 01f for the
dedicated validation behavior; here add it defensively so the field is serialized as an integer):

```python
if priority:
    try:
        priority_int = int(priority)
    except (TypeError, ValueError):
        return "ERROR: priority must be an integer"
    event.add("priority", priority_int)
```

### 3. Add attendees as `vCalAddress`

For each trimmed, non-empty email from the comma-separated `attendees` string, create a
`vCalAddress` with `mailto:` and attach parameters:

```python
if attendees:
    for email in attendees.split(","):
        email = email.strip()
        if not email:
            continue
        attendee = vCalAddress("mailto:" + email)
        attendee.params["PARTSTAT"] = vText("NEEDS-ACTION")
        attendee.params["RSVP"] = vText("TRUE")
        attendee.params["ROLE"] = vText("REQ-PARTICIPANT")
        event.add("attendee", attendee, encode=0)
```

### 4. Attach the event and save

```python
cal.add_component(event)
cal.save_event(cal.to_ical().decode("utf-8"))
```

Keep the existing `return "OK: Event '%s' created (uid=%s)" % (summary, uid)` line unchanged.

## Important details

- `dtstart`/`dtend` must stay as timezone-aware `datetime` objects produced by
  [`_parse_dt`](../server.py:66). Do **not** convert them back to strings via
  [`_format_ical_dt`](../server.py:89) here; `icalendar` handles datetime serialization and
  escaping correctly.
- `to_ical()` returns `bytes`; decode to a `str` with `utf-8` before passing to `save_event`.
- Do **not** remove [`_format_ical_dt`](../server.py:89) in this sub-step; it is still referenced
  elsewhere and its removal is out of scope.
- `event.add("attendee", attendee, encode=0)` prevents `icalendar` from double-encoding the
  already-prepared `mailto:` address.

## Definition of done

- No `"BEGIN:VEVENT"` / `"END:VEVENT"` / `"BEGIN:VCALENDAR"` string concatenation remains in
  [`caldav_create_event`](../server.py) — verified by inspecting the function body.
- The function builds a [`Calendar`](../server.py:1) containing an [`Event`](../server.py:1) and
  serializes via `cal.to_ical()`.
- `summary`, `location`, `description`, `categories`, and `attendees` are added through the
  component API (escaped by `icalendar`), not raw interpolation.

## Constraints

- Do **not** modify function signature or docstring.
- Do **not** change [`_parse_dt`](../server.py:66), [`_format_ical_dt`](../server.py:89), or any
  other function in this sub-step.
- Do **not** add typing, tests, or lint changes here; those are later sub-steps.
