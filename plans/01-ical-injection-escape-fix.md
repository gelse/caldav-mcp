# Plan: Fix iCal string injection / missing RFC 5545 escaping

## Problem

[`caldav_create_event`](../server.py:274) builds the VCALENDAR/VEVENT payload via naive string
concatenation. `summary`, `location`, `description`, and `categories` (and `attendees`) are
interpolated directly without RFC 5545 escaping. Special characters (`\n`, `,`, `;`, `\`) and
line breaks corrupt the payload, producing invalid iCal that the server may reject or store
incorrectly. `priority`/`rrule` are also injected raw.

## Goal

Construct VEVENT payloads through the `icalendar` library (already present transitively via
`caldav`) so that all values are properly escaped and the component is always serialized to
valid iCal, rather than hand-building text.

## Steps

1. Add `icalendar` as an explicit top-level dependency in [`pyproject.toml`](../pyproject.toml:6)
   and [`requirements.txt`](../requirements.txt:1) (pin a version).
2. Replace the `ical_parts` string list in [`caldav_create_event`](../server.py:275) with an
   `icalendar.Calendar` + `icalendar.Event` and set properties via the component API
   (`event.add('summary', summary)`, `event.add('dtstart', start_dt)`, etc.).
3. Use `vCalAddress` for attendees to correctly emit `mailto:` values with `PARTSTAT`/`ROLE`/`RSVP`.
4. Serialize via `cal.to_ical()` and pass to `cal.save_event(...)`.
5. Validate `priority` is an integer in `0..9` and that `rrule` parses (or leave to library) before
   adding.
6. Add unit tests covering edge cases: summary with comma/backslash/newline, multiple attendees,
   emoji, empty optional fields.

## Affected files

- `server.py` (`caldav_create_event`)
- `pyproject.toml`, `requirements.txt`
- new test file

## Acceptance criteria

- No raw `"BEGIN:VEVENT"` string concatenation remains in `caldav_create_event`.
- A summary containing `,` `;` `\` or a newline produces a valid, round-trippable event.
- `icalendar` is declared as a direct dependency.
