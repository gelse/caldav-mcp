# Plan 03c — Add unit tests for the auth guard

> Parent plan: [`03-mcp-auth-endpoint-security.md`](03-mcp-auth-endpoint-security.md)
> Prerequisites: plans 03a (guard helper) and 03b (guard wired into tools).
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Add unit tests covering `_require_auth()` and the fact that guarded tools reject
unauthenticated requests.

## Context you must know

- Tests live in [`tests/`](../tests) and are run with the standard library runner:
  `python -m unittest discover -s tests -v`.
- Existing tests ([`tests/test_create_event.py`](../tests/test_create_event.py)) use
  `unittest` + `unittest.mock`, patching module-level functions in [`server.py`](../server.py)
  (e.g. `mock.patch.object(server, "_resolve_credentials", ...)`).
- `_require_auth()` reads `server.API_KEY` (module constant) and calls
  `server.get_http_headers()`. In tests you control both of these:
  - Patch `server.API_KEY` directly (or via `mock.patch.object(server, "API_KEY", ...)`).
  - Patch `server.get_http_headers` to return a dict of lowercase header keys.
- `_require_auth()` returns `""` on success, `""` when `API_KEY` is empty, and the
  string `"ERROR: unauthorized - missing or invalid API token"` on failure.
- The guard is wired so that when `_require_auth()` returns a non-empty string, the
  tool returns that string immediately (no credentials resolved, no network).

## Create the test file

Create `tests/test_auth.py`.

### Test 1 — disabled auth passes

- Patch `server.API_KEY` to `""`.
- Call `server._require_auth()`.
- Assert it returns `""`.

### Test 2 — valid `Authorization: Bearer <token>` passes

- Patch `server.API_KEY` to `"secret-token"`.
- Patch `server.get_http_headers` to return `{"authorization": "Bearer secret-token"}`.
- Assert `server._require_auth()` returns `""`.

### Test 3 — valid `X-Api-Key` passes

- Patch `server.API_KEY` to `"secret-token"`.
- Patch `server.get_http_headers` to return `{"x-api-key": "secret-token"}`.
- Assert `server._require_auth()` returns `""`.

### Test 4 — missing token fails

- Patch `server.API_KEY` to `"secret-token"`.
- Patch `server.get_http_headers` to return `{}`.
- Assert the result equals `"ERROR: unauthorized - missing or invalid API token"`.

### Test 5 — wrong token fails

- Patch `server.API_KEY` to `"secret-token"`.
- Patch `server.get_http_headers` to return `{"authorization": "Bearer wrong"}`.
- Assert the result equals the unauthorized error string.

### Test 6 — bearer case-insensitive scheme + malformed header

- Patch `server.API_KEY` to `"secret-token"`.
- Patch `server.get_http_headers` to return
  `{"authorization": "bearer secret-token"}` (lowercase scheme).
- Assert `server._require_auth()` returns `""` (scheme comparison is case-insensitive).

### Test 7 — a guarded tool rejects when unauthenticated, before credentials

- Patch `server.API_KEY` to `"secret-token"`.
- Patch `server.get_http_headers` to return `{}`.
- Patch `server._resolve_credentials` with a `mock.Mock` whose `side_effect` raises
  `AssertionError("credentials must not be resolved")`.
- Call `server.caldav_list_calendars()`.
- Assert the result is the unauthorized error string (not the AssertionError),
  proving the guard short-circuits before credential resolution.

## Style guidance

- Mirror the structure of [`tests/test_create_event.py`](../tests/test_create_event.py):
  `import unittest`, `from unittest import mock`, `import server`, one
  `unittest.TestCase` class, and `if __name__ == "__main__": unittest.main()`.
- For tests 1-6, use `mock.patch.object(server, "API_KEY", value)` and
  `mock.patch.object(server, "get_http_headers", return_value=...)` via
  `mock.patch.object(...)` context managers (`with ...:`) so patching is scoped and
  cleaned up automatically. Do not add setUp/tearDown complexity unless needed.
- Use exact assertion for the error string; define it once as a module-level constant
  in the test file to avoid duplication.

## Definition of done

- `tests/test_auth.py` exists and contains tests 1-7 above.
- Running `python -m unittest discover -s tests -v` passes all tests (existing and new).
- No source files under the project root (other than the new test) are modified.

## Constraints / rules

- Do NOT modify [`server.py`](../server.py), docs, or config in this step.
- Do NOT add new third-party test dependencies; use stdlib `unittest` + `mock`.
- Do NOT test FastMCP's internal HTTP behavior; only test `_require_auth()` and the
  tool short-circuit via patching.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Add auth guard unit tests
```
