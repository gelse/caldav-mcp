# Plan 01f: Add unit tests for `caldav_create_event` escaping

## Context

This is **sub-step 01f** of the overall plan
[`01-ical-injection-escape-fix.md`](./01-ical-injection-escape-fix.md). It adds unit tests that
verify special-character escaping and edge cases in the refactored
[`caldav_create_event`](../server.py:254).

## Current state

There is no `tests/` directory in the workspace. The project declares no explicit test framework
in [`pyproject.toml`](../pyproject.toml). The `icalendar` library (added in sub-step 01a) provides
a round-trip parser (`icalendar.Calendar.from_ical`) that is sufficient for these tests without any
additional test dependency.

## Change

Create a new test file `tests/test_create_event.py` using the standard-library `unittest` framework
(no new dependency required).

### 1. Import and isolation strategy

The `caldav_create_event` function performs CalDAV network calls (`_client`, `_resolve_credentials`,
`cal.save_event`). To test **only** the payload-building and escaping logic, do **not** call
`caldav_create_event` directly against a live server. Instead, extract-test the serialization by
reconstructing the component-building portion, or by monkeypatching the network boundaries.

Preferred approach: refactor-free test using `unittest.mock.patch` to stub the CalDAV boundary so
that `cal.save_event` captures the serialized iCal string. Patch the following inside
[`server.py`](../server.py):

- `server._resolve_credentials` to return a dummy `(url, user, pw)` tuple.
- `server._client` to return a fake client whose `principal().calendars()` returns a list containing
  a fake calendar with `name == ""` and a `save_event` method that stores the argument.
- `server._get_calendar` (or the fake calendar returned above) so the target calendar is selected.

Then call `caldav_create_event(...)` and inspect the captured payload with
`icalendar.Calendar.from_ical(payload)`.

### 2. Required test cases

Write assertions covering these cases:

1. **summary with comma, semicolon, backslash, and newline** — a summary like
   `"a,b;c\\d\ne"` must round-trip through `Calendar.from_ical` and yield a `SUMMARY` whose
   decoded text equals the original summary.
2. **location and description with special characters** — verify unescaped round-trip of a
   location with a comma and a description with a newline.
3. **multiple attendees** — `"a@example.com, b@example.com"` must produce two `ATTENDEE`
   properties, each a `vCalAddress` with value `maileto:...` (note: `icalendar` lowercases the
   scheme on round-trip to `mailto`), with `PARTSTAT=NEEDS-ACTION` and `RSVP=TRUE` params.
4. **emoji in summary/description** — verify non-ASCII (e.g. `"🎉 party"`) survives round-trip.
5. **empty optional fields** — with `location`, `description`, `categories`, `priority`, `rrule`,
   and `attendees` all empty, the event must still parse and contain `SUMMARY`, `UID`, `DTSTART`,
   `DTEND`, `DTSTAMP`, and no `ATTENDEE`/`LOCATION`/`DESCRIPTION`/`PRIORITY`/`RRULE`.
6. **valid priority** — `priority="5"` yields an integer `priority` with value `5`.
7. **invalid priority** — `priority="high"` returns an error string containing
   `"priority must be an integer"`, and `priority="10"` returns an error string containing
   `"priority must be between 0 and 9"`.
8. **invalid rrule** — `rrule="FREQ=BOGUS"` returns `"ERROR: invalid RRULE"` (or an error string).

### 3. Add test discovery to `pyproject.toml`

Add a test configuration so tests can be run with `pytest`/`unittest`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

Do **not** add `pytest` to dependencies unless it is already present; `unittest` works with
`python -m unittest discover -s tests`.

## Definition of done

- `tests/test_create_event.py` exists with the required test cases.
- Running `python -m unittest discover -s tests` (or `pytest` if available) passes all tests.
- A summary containing `,`, `;`, `\`, and a newline round-trips correctly (acceptance criterion
  from the parent plan).
- No raw `"BEGIN:VEVENT"` concatenation is exercised in the tested code path.

## Constraints

- Do **not** add new third-party dependencies solely for testing; use `unittest` + `unittest.mock`.
- Test only the escaping/serialization behavior; do **not** add unrelated tests here.
- Do **not** modify [`server.py`](../server.py) logic in this sub-step beyond what is needed for
  testability (prefer mocking over refactoring; if a helper must be extracted, note it explicitly
  before doing so).
