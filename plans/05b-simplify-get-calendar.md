# Plan 05b — Simplify `_get_calendar` empty-string/None handling

> Parent plan: [`05-unused-imports-cleanup.md`](05-unused-imports-cleanup.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Remove the redundant `calendar_name=""` default in [`_get_calendar`](server.py:53) so the
empty-string and `None` cases are handled in one place, without changing behavior.

## Context you must know

- [`_get_calendar`](server.py:53) is currently defined as:
  ```python
  def _get_calendar(client, calendar_name=""):
      calendars = client.principal().calendars()
      if not calendars:
          raise ValueError("No calendars found for this principal")
      if calendar_name:
          for c in calendars:
              if c.name == calendar_name:
                  return c
          raise ValueError(
              "Calendar '%s' not found. Available: " % calendar_name
              + ", ".join(c.name for c in calendars)
          )
      return calendars[0]
  ```
- Every caller passes `calendar_name or None` (e.g. [`server.py:194`](server.py:194),
  [`server.py:238`](server.py:238), etc.), so the default `""` is effectively never relied upon and
  overlaps with the `None`/empty branch ("return `calendars[0]`").
- Note: this step only simplifies the signature/logic. It must preserve the current behavior:
  no calendar_name → first calendar; named calendar not found → error message.
- Note on cross-plan interaction: plan 04b may have replaced the `ValueError` raises here with
  `NotFoundError`. Preserve whatever exception type is currently in place — do **not** revert or
  re-type those raises. This step only changes the default argument and the falsy check.

## Implementation steps

### Step 1 — Change the signature

Change the signature from `calendar_name=""` to `calendar_name=None`:

```python
def _get_calendar(client, calendar_name=None):
```

### Step 2 — Update the falsy branch guard

The existing `if calendar_name:` already treats `None` and `""` identically (both falsy), so the
body can stay the same. Confirm the `if calendar_name:` line remains the single place that
distinguishes "no name" from "named". No other changes are needed.

### Step 3 — Verify

Run:

```bash
python -c "import server"
python -m unittest discover -s tests -v
```

## Definition of done

- [`_get_calendar`](server.py:53) signature is `def _get_calendar(client, calendar_name=None)`.
- The empty/`None` case is handled by a single `if calendar_name:` branch returning
  `calendars[0]`.
- No exception type or message was changed (unless 04b already changed the type — leave it as-is).
- Import and test suite pass.

## Constraints / rules

- Do NOT alter the exception types/messages raised (this step is signature-only). If 04b already
  converted them to `NotFoundError`, keep `NotFoundError`.
- Do NOT change [`_client`](server.py:49) or any caller in this step.
- Do not deviate from this plan.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Simplify _get_calendar empty name handling
```
