# Plan 01g: Verify and finalize the escaping fix

## Context

This is the final **sub-step 01g** of the overall plan
[`01-ical-injection-escape-fix.md`](./01-ical-injection-escape-fix.md). It verifies the complete
change set, ensures the acceptance criteria are met, and confirms nothing else regressed.

## Verification steps

1. **Imports OK** — confirm module imports without error:

   ```bash
   python -c "import server"
   ```

2. **Dependency declared** — confirm `icalendar` is listed in both
   [`pyproject.toml`](../pyproject.toml) and [`requirements.txt`](../requirements.txt):

   ```bash
   grep -n "icalendar" pyproject.toml requirements.txt
   ```

3. **Tests pass** — run the new test suite:

   ```bash
   python -m unittest discover -s tests -v
   ```

   (or `pytest` if configured). All tests created in sub-step 01f must pass.

4. **No raw string concatenation remains** — search for the old pattern in
   [`server.py`](../server.py) and confirm it is absent from `caldav_create_event`:

   ```bash
   grep -n "BEGIN:VEVENT\|ical_parts" server.py
   ```

   The only remaining `icalendar`-related string usage should be unrelated code (e.g. the
   `caldav_add_attendee` / `caldav_remove_attendee` string handling, which are explicitly out of
   scope for this plan).

5. **Round-trip check** — manually (or via a one-off snippet) construct a summary containing
   `,`, `;`, `\`, and a newline and confirm `icalendar.Calendar.from_ical(...)` recovers it
   exactly.

6. **Lint/type no-regression** — if the project has configured lint/type tooling, run it on
   `server.py` and fix only issues introduced by this change (do not fix pre-existing unrelated
   issues — those are covered by other plans in this directory).

## Definition of done

- All three acceptance criteria from the parent plan are satisfied:
  - No raw `"BEGIN:VEVENT"` string concatenation remains in `caldav_create_event`.
  - A summary containing `,`, `;`, `\`, or a newline produces a valid, round-trippable event.
  - `icalendar` is declared as a direct dependency.
- The test suite passes.
- A short git commit is created summarizing the change (e.g. `Escape iCal fields via icalendar`).

## Constraints

- Do **not** make unrelated changes to other plans' scope (timezone handling, error handling,
  attendee string parsing, etc.).
- Do **not** remove [`_format_ical_dt`](../server.py:89) or other helpers unless they are now
  provably unreferenced and removal is explicitly safe; otherwise leave them for their dedicated
  cleanup plans.
