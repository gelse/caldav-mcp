# Plan: Validate `priority` and `rrule` inputs

## Problem

[`caldav_create_event`](../server.py:262) accepts `priority` and `rrule` and injects them raw into
the iCal payload without validation. `PRIORITY` must be `0`–`9`, and a malformed `RRULE` silently
produces a corrupt event.

## Goal

Validate (or delegate to the library) these fields so invalid input produces a clear error rather
than corrupt data.

## Steps

1. Validate `priority`: must parse as an integer in `0..9` (or empty); reject otherwise with a
   clear message.
2. Validate `rrule` using the `icalendar`/`dateutil` recurrence parser (or a minimal sanity check)
   before adding; reject malformed rules.
3. Fold this into the issue #01 refactor (adding via the `icalendar` component API), which will
   also handle escaping/typing of these values.
4. Add unit tests for valid and invalid `priority`/`rrule`.

## Affected files

- `server.py` (`caldav_create_event`)

## Acceptance criteria

- Invalid `priority` (non-integer or out of range) is rejected with a clear error.
- Malformed `rrule` is rejected rather than written.
