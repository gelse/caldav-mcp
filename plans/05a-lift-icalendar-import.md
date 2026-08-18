# Plan 05a — Lift `icalendar` imports to top level and remove unused names

> Parent plan: [`05-unused-imports-cleanup.md`](05-unused-imports-cleanup.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Remove the function-local `from icalendar import Calendar, Event as IEvent` inside
[`caldav_update_event`](server.py:333), which imports unused names (`Calendar`, `IEvent`). The
top-level module already imports the needed `icalendar` names, so this eliminates the redundant
import without changing behavior.

## Context you must know

- The top of [`server.py`](server.py:7) already imports:
  ```python
  from icalendar import Calendar, Event
  from icalendar import vCalAddress, vText
  ```
- Inside [`caldav_update_event`](server.py:348) there is a **redundant** import:
  ```python
  from icalendar import Calendar, Event as IEvent
  ```
  Neither `Calendar` nor `IEvent` is used in that function; the function uses `_comp(event)` and
  mutates `comp[...]`.
- `icalendar` is already declared as a direct dependency in
  [`pyproject.toml`](pyproject.toml:7) (`"icalendar>=6.0.0"`) and
  [`requirements.txt`](requirements.txt:1) (`icalendar>=6.0.0`).

## Implementation steps

### Step 1 — Delete the local import

In [`caldav_update_event`](server.py:348), delete the line:

```python
        from icalendar import Calendar, Event as IEvent
```

Do not add any replacement; the function relies only on `_comp(event)` and already-available
module state.

### Step 2 — Verify

Run:

```bash
python -c "import server"
```

and the test suite:

```bash
python -m unittest discover -s tests -v
```

Confirm no `NameError`/`UnboundLocalError` is introduced (the function must still import and run).

## Definition of done

- The function-local `from icalendar import Calendar, Event as IEvent` is removed from
  [`caldav_update_event`](server.py:333).
- `icalendar` remains declared in both [`pyproject.toml`](pyproject.toml:7) and
  [`requirements.txt`](requirements.txt:1) (already present; do not remove).
- `python -c "import server"` and the test suite succeed.

## Constraints / rules

- Do NOT touch the top-level `icalendar` imports at [`server.py:7-8`](server.py:7).
- Do NOT modify dependency files in this step (already correct).
- Do NOT change any function logic or other functions.
- Do not deviate from this plan; only remove the redundant local import.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Remove redundant local icalendar import
```
