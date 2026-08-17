# Plan: Remove unused imports and redundant code

## Problem

- `from icalendar import Calendar, Event as IEvent` inside [`caldav_update_event`](../server.py:325)
  imports `Calendar` and `IEvent`, neither of which is used (the code manipulates the component
  directly). The import should move to the top level and become an explicit dependency, or be
  removed.
- `CalDAVError` ([`server.py:29`](../server.py:29)) is raised only in `_resolve_credentials` but
  always caught as `Exception`, so it conveys no information.
- `_get_calendar(client, calendar_name or None)` — the `calendar_name=""` default overlaps with the
  `None` branch and is redundant.
- `_client` wrapper adds indirection with no benefit.

## Goal

Clean up dead/inert code while keeping behavior identical.

## Steps

1. Lift `from icalendar import ...` to the module top-level and declare `icalendar` in
   [`pyproject.toml`](../pyproject.toml:6) / [`requirements.txt`](../requirements.txt:1).
2. Remove the unused `Calendar` / `IEvent` names (or use them as part of issue #01 refactor).
3. Consolidate credential resolution so `CalDAVError` (or its replacement) is meaningfully
   distinguishable (see issue #04).
4. Simplify `_get_calendar` so empty string and `None` are handled in one place.
5. Inline or remove `_client` if it adds no value.

## Affected files

- `server.py`

## Acceptance criteria

- No unused imports/warnings under a linter (e.g. `ruff`).
- `icalendar` declared as a direct dependency.
