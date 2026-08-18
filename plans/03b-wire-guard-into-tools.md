# Plan 03b — Wire the `_require_auth()` guard into every tool function

> Parent plan: [`03-mcp-auth-endpoint-security.md`](03-mcp-auth-endpoint-security.md)
> Prerequisite: plan 03a (the `_require_auth()` helper already exists in `server.py`).
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Make every `@mcp.tool()` function reject unauthenticated requests by calling
`_require_auth()` as the first action in its body.

## Context you must know

There are **14** tool functions in [`server.py`](../server.py). They all begin their
body inside a `try:` block by calling `_resolve_credentials()`. The guard must run
**before** any credentials are resolved or any network call is made.

The tool functions (and their `try:` opening lines) are:

1. `caldav_list_calendars` — [`server.py:176`](../server.py:176)
2. `caldav_get_events` — [`server.py:189`](../server.py:189)
3. `caldav_get_today_events` — [`server.py:211`](../server.py:211)
4. `caldav_get_week_events` — [`server.py:222`](../server.py:222)
5. `caldav_get_event_by_uid` — [`server.py:233`](../server.py:233)
6. `caldav_create_event` — [`server.py:256`](../server.py:256)
7. `caldav_update_event` — [`server.py:333`](../server.py:333)
8. `caldav_add_attendee` — [`server.py:370`](../server.py:370)
9. `caldav_remove_attendee` — [`server.py:391`](../server.py:391)
10. `caldav_list_attendees` — [`server.py:416`](../server.py:416)
11. `caldav_delete_event` — [`server.py:432`](../server.py:432)
12. `caldav_move_event` — [`server.py:446`](../server.py:446)
13. `caldav_search_events` — [`server.py:465`](../server.py:465)
14. `caldav_get_freebusy` — [`server.py:487`](../server.py:487)

Note: `caldav_get_today_events` and `caldav_get_week_events` are thin wrappers that
delegate to `caldav_get_events`; they do not have their own `try/except`. Because
`caldav_get_events` is guarded, these wrappers are transitively protected. However,
you MUST still insert the guard in these two wrappers so they fail fast with the
same auth error **before** delegating (and to cover the case where the delegated
function signature changes later).

## Implementation steps

For **each** of the 14 tool functions, insert the guard call as the **first line of
the function body** (inside the `try:` block where one exists, immediately after
the docstring). The exact pattern:

```python
    error = _require_auth()
    if error:
        return error
```

### Pattern A — functions with `try/except` (most functions)

Insert after `try:` and before `url, user, pw = _resolve_credentials()`. Example for
`caldav_list_calendars`:

```python
@mcp.tool()
def caldav_list_calendars() -> str:
    """List all calendars available for the configured account."""
    try:
        error = _require_auth()
        if error:
            return error
        url, user, pw = _resolve_credentials()
        ...
```

### Pattern B — wrapper functions without `try/except`

For `caldav_get_today_events` ([`server.py:211`](../server.py:211)) and
`caldav_get_week_events` ([`server.py:222`](../server.py:222)), insert the guard as
the first lines after the docstring, before constructing the date range. Example for
`caldav_get_today_events`:

```python
@mcp.tool()
def caldav_get_today_events(calendar_name: str = "") -> str:
    """Get events for today (00:00 to 24:00)."""
    error = _require_auth()
    if error:
        return error
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return caldav_get_events(...)
```

Apply this Pattern B to both wrapper functions.

### Do not change anything else

- Do NOT alter the `except Exception as e: return "ERROR: %s" % e` blocks.
- Do NOT change the return types, signatures, or docstrings (other than inserting
  the guard lines).
- Do NOT refactor, reorder imports, or rename anything.

## Definition of done

- All 14 tool functions call `_require_auth()` at the very top of their body and
  return its result immediately when non-empty.
- The guard is the **first executable statement** in each tool (before credential
  resolution / before delegating).
- A request with a missing/invalid token returns `ERROR: unauthorized - missing or
  invalid API token` from any tool.
- No behavior change occurs for valid or unconfigured (empty `API_KEY`) cases.
- Only [`server.py`](../server.py) is modified.

## Constraints / rules

- Do NOT touch [`server.py`](../server.py) imports or helpers (they were finalized in 03a).
- Do NOT add tests, docs, or env/compose changes in this step.
- Keep the same `ERROR:` return-string convention for the auth failure.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Enforce auth guard in all tool functions
```
