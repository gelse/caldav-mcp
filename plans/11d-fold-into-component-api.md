# Plan 11d — Fold validation into component-API refactor

> Parent plan: [`11-priority-rrule-validation.md`](./11-priority-rrule-validation.md)

## Objective

Ensure the priority and rrule validation helpers (11a/11b) are integrated with the issue #01
component-API refactor of `caldav_create_event`, so all fields — including `priority` and `rrule` —
are added via the `icalendar` component API with consistent escaping/typing.

## Context you must know

The parent plan's step 3 says: "Fold this into the issue #01 refactor (adding via the `icalendar`
component API), which will also handle escaping/typing of these values." Issue #01 corresponds to
[`01-ical-injection-escape-fix.md`](./01-ical-injection-escape-fix.md) and its sub-plans (01c in
particular reworks the create-event body to use `event.add(...)`).

Current state of `caldav_create_event` ([`server.py:256`](../server.py:256)):

- `priority` is validated then added via `event.add("priority", priority_int)` ([`server.py:303`](../server.py:303)).
- `rrule` is validated then added via `event.add("rrule", rrule)` ([`server.py:312`](../server.py:312)).

After 11a/11b, both are added via the component API already. This sub-plan is a **verification and
reconciliation** step: confirm the create-body refactor (issue #01) and the validation refactor are
consistent, and that `rrule` is stored as a proper `vRecur` value where the API supports it.

## Chosen mechanism (do not deviate)

- Keep `event.add(...)` for both fields (no raw string concatenation).
- If the `icalendar` component API supports a typed `vRecur` value for `rrule`, prefer adding the
  parsed `vRecur` object rather than the raw string; otherwise keep the raw string and document why.

## Implementation steps

### Step 1 — Review the create-body against issue #01

Open [`server.py:256`](../server.py:256)-323 and confirm every field (summary, dtstart, dtend,
location, description, categories, priority, rrule, attendees) is added via `event.add(...)`. If any
field is still injected via raw string manipulation, note it (it belongs to issue #01, not here).

### Step 2 — Reconcile `rrule` addition

Change `event.add("rrule", rrule)` to add the parsed recurrence object if `icalendar` accepts it
cleanly and round-trips. Prefer:

```python
event.add("rrule", vRecur.from_ical(rrule))
```

but only if round-trip tests (11c / issue #07) confirm no regression. Otherwise keep the raw string
and record the reason.

### Step 3 — Confirm validation ordering

Ensure validation (11a/11b) still runs **before** `event.add` for both fields, so invalid input never
reaches the component.

### Step 4 — Run tests

```bash
python -m unittest discover -s tests -v
pytest -q
```

## Definition of done

- [ ] All create-event fields (including priority and rrule) go through `event.add(...)`.
- [ ] `rrule` uses the parsed `vRecur` where verified safe, else raw string with a documented reason.
- [ ] Validation runs before insertion; invalid input is rejected.
- [ ] Full suite passes.

## Constraints / rules

- Only touch `caldav_create_event`; do not alter issue #01's escaping fixes for other fields.
- If a change risks a regression, leave `rrule` as a raw string and document it.

## Commit

```bash
git add server.py && git commit -m "Fold priority and rrule validation into component API"
```
