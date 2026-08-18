# Plan 05c — Inline `_client` helper

> Parent plan: [`05-unused-imports-cleanup.md`](05-unused-imports-cleanup.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Remove the thin [`_client`](server.py:49) wrapper (a one-line `DAVClient(...)` constructor call
that adds indirection without benefit) by inlining its single call site behavior into each tool,
keeping behavior identical.

## Context you must know

- [`_client`](server.py:49) is:
  ```python
  def _client(url, username, password):
      return DAVClient(url=url, username=username, password=password)
  ```
- Every tool calls it once, e.g. [`caldav_get_events`](server.py:193):
  ```python
  client = _client(url, user, pw)
  ```
- The function is a pure indirection over `DAVClient(...)` (imported at
  [`server.py:6`](server.py:6)). Inlining preserves behavior exactly.

## Implementation steps

### Step 1 — Inline the constructor at each call site

For each occurrence of `client = _client(url, user, pw)`, replace it with:

```python
        client = DAVClient(url=url, username=user, password=pw)
```

The affected call sites are the tools listed in the parent plan's "Affected files"; concretely
every `@mcp.tool` that does `_client(url, user, pw)`, including (non-exhaustively)
[`caldav_list_calendars`](server.py:180), [`caldav_get_events`](server.py:193),
[`caldav_get_event_by_uid`](server.py:237), [`caldav_create_event`](server.py:271),
[`caldav_update_event`](server.py:345), [`caldav_add_attendee`](server.py:374),
[`caldav_remove_attendee`](server.py:395), [`caldav_list_attendees`](server.py:420),
[`caldav_delete_event`](server.py:436), [`caldav_move_event`](server.py:450),
[`caldav_search_events`](server.py:469), [`caldav_get_freebusy`](server.py:491).

Search for `_client(` to ensure you replace all occurrences.

### Step 2 — Delete the `_client` function

Delete the entire [`_client`](server.py:49) function definition.

### Step 3 — Verify

Run:

```bash
python -c "import server"
python -m unittest discover -s tests -v
```

## Definition of done

- No `_client(` references remain in [`server.py`](server.py) (search confirms zero).
- Each former call site now constructs `DAVClient(url=url, username=user, password=pw)` inline.
- Import and test suite pass.

## Constraints / rules

- Preserve the exact keyword arguments `url=url`, `username=user`, `password=pw` (the local
  variable names are `user`, `pw` after `_resolve_credentials()` unpacks).
- Do NOT change `_resolve_credentials` or argument ordering.
- Do not deviate from this plan; only inline and remove `_client`.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Inline _client helper
```
