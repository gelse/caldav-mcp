# Plan 04c — Add error-handling tests and verify

> Parent plan: [`04-broad-except-error-handling.md`](04-broad-except-error-handling.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Add unit tests that verify the new error-classification behavior from 04a/04b, then run the full
suite to confirm no regressions. Assumes 04a and 04b are complete.

## Context you must know

- Plan 04a added `AuthError`, `NotFoundError`, `ServerError`, `log`, and `_log_exception`.
- Plan 04b converted the tool handlers and `_resolve_credentials`/`_get_calendar` to raise and
  catch typed exceptions, producing result prefixes `ERROR:[auth]`, `ERROR:[not_found]`, and
  `ERROR:[server]`.
- Existing tests live in [`tests/test_create_event.py`](tests/test_create_event.py) and use the
  stdlib `unittest` framework with `mock.patch.object` to stub
  `_resolve_credentials`, `_client`, and `_get_calendar` (network-free).

## Implementation steps

### Step 1 — Create `tests/test_error_handling.py`

Create [`tests/test_error_handling.py`](tests/test_error_handling.py) using the stdlib
`unittest` framework, matching the existing test style (`import unittest`, `from unittest import
mock`, `import server`). It must be network-free: test only pure classification and helper
behavior by stubbing `_resolve_credentials`, `_client`, and `_get_calendar`.

Cover, at minimum:

1. **Not-found classification** — patch `server._get_calendar` to `raise
   server.NotFoundError("Calendar 'x' not found")` (via `mock.patch.object(..., side_effect=...)`)
   and patch `_resolve_credentials`/`_client` so the call reaches that point. Call
   `server.caldav_get_events(...)` and assert the result `startswith("ERROR:[not_found]")`.

2. **Auth classification** — patch `server._resolve_credentials` with
   `side_effect=server.AuthError("missing credentials")` and call `server.caldav_list_calendars()`;
   assert the result `startswith("ERROR:[auth]")`.

3. **Server-error classification + logging** — patch `server._resolve_credentials` to raise a
   generic `RuntimeError("boom")`, and patch `server._log_exception` (or the logger) to capture
   the call. Call a tool and assert:
   - the returned string `startswith("ERROR:[server]")`, and
   - `_log_exception` was called (or `log.exception` was invoked) — this confirms unexpected
     errors are logged rather than swallowed.

   Example assertion approach:
   ```python
   with mock.patch.object(server, "_resolve_credentials",
                          side_effect=RuntimeError("boom")), \
        mock.patch.object(server, "_log_exception",
                          return_value="ERROR:[server] Internal error") as m:
       result = server.caldav_list_calendars()
   self.assertTrue(result.startswith("ERROR:[server]"))
   m.assert_called_once()
   ```

4. **No credential leakage** — assert that `str(result)` does not contain a fake password value
   used in the failure, e.g. with a `RuntimeError` that would otherwise embed a secret if
   someone naively did `"ERROR: %s" % e`.

Each test must be a method on a `unittest.TestCase` subclass, and the file must end with:

```python
if __name__ == "__main__":
    unittest.main()
```

### Step 2 — Verify

Run the full suite from the workspace root:

```bash
python -m unittest discover -s tests -v
```

Confirm all tests (existing [`tests/test_create_event.py`](tests/test_create_event.py) plus the
new [`tests/test_error_handling.py`](tests/test_error_handling.py)) pass.

Also run an import sanity check:

```bash
python -c "import server; print(server.AuthError, server.NotFoundError, server._log_exception)"
```

## Definition of done

- [`tests/test_error_handling.py`](tests/test_error_handling.py) exists and passes.
- Coverage includes not-found (`ERROR:[not_found]`), auth (`ERROR:[auth]`), server-error
  (`ERROR:[server]`), logging-call confirmation, and no-credential-leakage assertions.
- The full suite passes via `python -m unittest discover -s tests -v`.

## Constraints / rules

- Use only the stdlib (`unittest`, `unittest.mock`); do not add new dependencies.
- Tests must be deterministic and require no live CalDAV server or network.
- Do NOT modify [`server.py`](server.py) in this step (code changes were done in 04a/04b).
- Do not deviate from this plan; only implement what is specified here.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Add error handling unit tests
```
