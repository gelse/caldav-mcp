# Plan: Add tests and static-analysis configuration

## Problem

The project has no test suite despite non-trivial parsing/serialization logic
(`_parse_dt`, `_event_to_dict`, `_attendee_str`, iCal construction). No linting or typing config
either.

## Goal

Add unit tests for the pure functions and introduce linting/type-checking.

## Steps

1. Add a test framework (e.g. `pytest`) and a `tests/` directory.
2. Cover `_parse_dt` for all accepted formats, `Z` suffix, date-only, invalid input, and timezone
   handling.
3. Cover `_event_to_dict` and `_attendee_str` with representative `icalendar` components.
4. Cover event/attendee serialization/round-trip (ties into issues #01 and #07) using a mock or
   in-memory CalDAV client.
5. Add `ruff` and `mypy` (or `pyright`) configuration, with the tool entry registered in CI (or a
   local pre-commit).
6. Fix any lint/type errors surfaced.

## Affected files

- new `tests/` directory
- new `pyproject.toml` `[tool.ruff]` / `[tool.mypy]` sections and dev dependencies
- optional CI workflow

## Acceptance criteria

- `pytest` passes with meaningful coverage of parsing/serialization helpers.
- `ruff` and type-check cleanly.
