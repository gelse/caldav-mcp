# Plan 07d — Add attendee round-trip tests and verify

> Parent plan: [`07-attendee-string-parsing.md`](07-attendee-string-parsing.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Add unit tests for attendee add/remove and event move round-trips, then run the full suite.
Assumes 07a, 07b, and 07c are complete.

## Context you must know

- Existing tests in [`tests/test_create_event.py`](tests/test_create_event.py) use stdlib
  `unittest` with a `FakeCalendar`/`FakeClient` harness and `mock.patch.object` to stub
  `_resolve_credentials`, `_client`, and `_get_calendar`.
- The new behavior: attendees/UID are manipulated via the `icalendar` component, not raw text.
- A reusable fake should expose an `event_by_uid(uid)` returning a fake event whose
  `icalendar_component` is a real `icalendar.Event` (so `_comp(event)` returns a usable
  component) and whose `save()`/`delete()` record side effects.

## Implementation steps

### Step 1 — Add a fake event with a real icalendar component

In a new [`tests/test_attendee_parsing.py`](tests/test_attendee_parsing.py), add a `FakeEvent`
class that:

- builds an `icalendar.Event` with a `UID`, `SUMMARY`, and one or two initial `ATTENDEE`
  entries,
- exposes `icalendar_component` (so `_comp` works),
- has `save()` and `delete()` methods that record calls,
- has a `data` attribute backed by `comp.to_ical().decode("utf-8")` (updated on save).

### Step 2 — Test `caldav_add_attendee`

Patch `_resolve_credentials`/`_client`/`_get_calendar` so `cal.event_by_uid(uid)` returns the
`FakeEvent`. Call `server.caldav_add_attendee(uid, "new@example.com", ...)` and assert:

- result starts with `"OK:"`,
- the component now has the new `ATTENDEE` with `mailto:new@example.com` and
  `ROLE`/`PARTSTAT=NEEDS-ACTION`/`RSVP=TRUE`.

### Step 3 — Test `caldav_remove_attendee`

Pre-populate an attendee `mailto:remove@example.com`. Call
`server.caldav_remove_attendee(uid, "remove@example.com")` and assert:

- result starts with `"OK:"`,
- the attendee is gone from the component.
- Also test the not-found case: removing a non-existent email returns `"not found"`.

### Step 4 — Test `caldav_move_event`

Patch `_get_calendar` to return a source calendar for `source_calendar` and a destination
calendar for `target_calendar` (both fake calendars). Call
`server.caldav_move_event(uid, target_calendar, source_calendar)` and assert:

- the destination calendar received a serialized payload (`save_event` called),
- the serialized payload's `UID` differs from the original,
- the original event was deleted.

### Step 5 — Verify

Run:

```bash
python -m unittest discover -s tests -v
```

Each test class must end with:

```python
if __name__ == "__main__":
    unittest.main()
```

## Definition of done

- [`tests/test_attendee_parsing.py`](tests/test_attendee_parsing.py) exists and passes.
- Add/remove/move round-trips are covered without a live CalDAV server.
- The full existing suite plus the new tests pass via `python -m unittest discover -s tests -v`.

## Constraints / rules

- Use only stdlib (`unittest`, `unittest.mock`) and `icalendar`; no new dependencies.
- Tests must be deterministic and network-free.
- Do NOT modify [`server.py`](server.py) in this step (code changes were done in 07a-07c).
- Do not deviate from this plan.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Add attendee and move round-trip tests
```
