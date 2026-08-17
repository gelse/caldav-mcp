# Plan: Replace raw attendee string manipulation with the component API

## Problem

[`caldav_add_attendee`](../server.py:354), [`caldav_remove_attendee`](../server.py:379), and
[`caldav_move_event`](../server.py:433) operate on `event.data` (raw iCal text) using
`.replace()` and line filtering. This is brittle: line-folding of long `ATTENDEE` lines, case
sensitivity, and `mailto:` variants cause missed matches or corrupt payloads.

## Goal

Manipulate attendees and UID through the parsed `icalendar` component rather than raw text.

## Steps

1. Use `event.icalendar_component` (already available via `_comp`) to read/add/remove `ATTENDEE`
   entries and to update `UID`.
2. For `caldav_add_attendee`, append a `vCalAddress` with `ROLE`/`PARTSTAT`/`RSVP` params instead
   of inserting a text line.
3. For `caldav_remove_attendee`, filter the component's `attendee` list by normalized value instead
   of substring line matching.
4. For `caldav_move_event`, set a new `UID` on the component and serialize, then save to the
   destination calendar, instead of `.replace("UID:" + uid, ...)`.
5. Persist via `event.save()` or the component's serialized bytes.
6. Add unit tests for add/remove/move round-trips.

## Affected files

- `server.py` (`caldav_add_attendee`, `caldav_remove_attendee`, `caldav_move_event`)

## Acceptance criteria

- No raw `.replace()`/`splitlines()` iCal text surgery remains for attendees/UID.
- Attendee add/remove/move works with folded lines and case variants.
