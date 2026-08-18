# Plan 10d — Add serialization/round-trip tests with a mock client

> Parent plan: [`10-tests-linting-typing.md`](./10-tests-linting-typing.md)

## Objective

Add round-trip tests for event and attendee serialization using a mock (in-memory) CalDAV client,
covering create → read of the same event. This ties into issues #01 (escaping) and #07 (attendee
parsing/serialization).

## Context you must know

The existing [`tests/test_create_event.py`](../tests/test_create_event.py) already demonstrates the
mock pattern: `FakeCalendar`, `FakePrincipal`, `FakeClient`, and a `patch_network(fake_cal)` helper
that patches `_resolve_credentials`, `_client`, and `_get_calendar` (lines 15-48). Reuse or mirror
that pattern rather than inventing a new one.

`caldav_create_event` is at [`server.py:256`](../server.py:256). It builds an `icalendar.Event`, sets
fields, and calls `calendar.add_event(...)` (or similar) before returning an error/empty string or the
result. `caldav_get_event_by_uid` is at [`server.py:233`](../server.py:233).

## Chosen mechanism (do not deviate)

- Create `tests/test_roundtrip.py` that reuses the fake-network pattern from `test_create_event.py`.
- After calling `caldav_create_event`, retrieve the stored event from the fake calendar and assert the
  serialized fields (summary with special chars, attendees) are present and correctly escaped.

## Implementation steps

### Step 1 — Read the existing mock pattern

Open [`tests/test_create_event.py`](../tests/test_create_event.py) and note how `FakeCalendar`,
`FakePrincipal`, `FakeClient`, and `patch_network` are defined and used (lines 15-48).

### Step 2 — Create `tests/test_roundtrip.py`

Copy/adapt the fake classes so the fake calendar **stores** events it receives (e.g. append to a
`self.events` list) instead of discarding them.

### Step 3 — Write the round-trip test

In a `unittest.TestCase`:

1. Patch the network to return your fake calendar.
2. Call `server.caldav_create_event(...)` with a summary containing characters that require RFC 5545
   escaping (e.g. `Back\slash, semicolon; newline`), and one or more attendees.
3. Assert the call returns success (`""` or the expected empty success marker).
4. Retrieve the stored `icalendar_component` from the fake calendar and assert:
   - the summary round-trips (escaped/unescaped correctly), and
   - the `ATTENDEE` lines match the input emails.

### Step 4 — Run tests

```bash
python -m unittest discover -s tests -v
pytest -q
```

## Definition of done

- [ ] `tests/test_roundtrip.py` exists and uses the fake-network mock pattern.
- [ ] A create → read round trip asserts escaped summary and attendees round-trip correctly.
- [ ] Full suite passes via `unittest` and `pytest`.

## Constraints / rules

- Do not modify [`server.py`](../server.py).
- Mirror the existing mock style in `test_create_event.py`; do not introduce a new mocking library.

## Commit

```bash
git add tests/test_roundtrip.py && git commit -m "Add serialization round-trip tests"
```
