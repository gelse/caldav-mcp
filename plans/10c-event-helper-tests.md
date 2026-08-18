# Plan 10c — Add `_event_to_dict` and `_attendee_str` unit tests

> Parent plan: [`10-tests-linting-typing.md`](./10-tests-linting-typing.md)

## Objective

Add unit tests for the pure serialization helpers `_event_to_dict` and `_attendee_str` in
[`server.py`](../server.py), using representative `icalendar` components.

## Context you must know

- `_event_to_dict(event)` is at [`server.py:125`](../server.py:125). It extracts fields (uid, summary,
  start, end, location, description, etc.) from an event object with an `icalendar_component`.
- `_attendee_str(attendee)` is at [`server.py:158`](../server.py:158). It renders an attendee object to
  a readable string (email + optional CN/role).
- `_comp(event)` at [`server.py:120`](../server.py:120) returns `getattr(event, "icalendar_component", None)`.
- The `icalendar` library exposes `icalendar.Calendar`, `icalendar.Event`, and `icalendar.vCalAddress`
  for constructing components.

Read the actual implementations of `_event_to_dict` and `_attendee_str` before writing assertions, and
assert against their real output shape (dict keys, string format).

## Chosen mechanism (do not deviate)

- Create `tests/test_event_helpers.py` using `unittest.TestCase`.
- Build `icalendar.Event` objects directly (no network/mock client needed) and pass lightweight
  wrappers exposing `icalendar_component` to satisfy `_comp`.

## Implementation steps

### Step 1 — Create the test file and a minimal fixture

Create `tests/test_event_helpers.py`. Define a small helper that builds an `icalendar.Event`
component and a wrapper object exposing `icalendar_component`:

```python
import unittest
from icalendar import Calendar, Event, vCalAddress
import server

def make_event_wrapper(component):
    class Wrapper:
        def __init__(self, comp):
            self.icalendar_component = comp
    return Wrapper(component)
```

### Step 2 — Test `_event_to_dict`

Construct an `Event` with a `UID`, `SUMMARY`, `DTSTART`, `DTEND`, `LOCATION`, and `DESCRIPTION`, add
it to a `Calendar`, and assert `_event_to_dict(wrapper)` returns a dict whose keys/values match the
implementation. Use `server._event_to_dict` and assert the exact field names the function emits.

### Step 3 — Test `_attendee_str`

Construct a `vCalAddress` (e.g. `mailto:alice@example.com`) with a `CN` parameter, wrap it as the
implementation expects, and assert `server._attendee_str(...)` returns the expected string. Add a
second case without `CN` to cover the default branch.

### Step 4 — Run tests

```bash
python -m unittest discover -s tests -v
pytest -q
```

## Definition of done

- [ ] `tests/test_event_helpers.py` exists.
- [ ] `_event_to_dict` output is asserted against the implementation's actual keys.
- [ ] `_attendee_str` is covered with and without a `CN` param.
- [ ] Full suite passes via `unittest` and `pytest`.

## Constraints / rules

- Do not modify [`server.py`](../server.py). Report (do not fix) any discovered bugs.
- Use the real `icalendar` component API; do not mock `icalendar`.

## Commit

```bash
git add tests/test_event_helpers.py && git commit -m "Add event helper unit tests"
```
