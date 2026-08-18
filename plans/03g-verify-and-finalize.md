# Plan 03g — Verify and finalize the authentication change

> Parent plan: [`03-mcp-auth-endpoint-security.md`](03-mcp-auth-endpoint-security.md)
> Prerequisites: plans 03a through 03f complete.
> This is the final verification step. Implement ONLY what is described here.

## Objective

Verify the whole authentication change end-to-end and confirm all acceptance
criteria from the parent plan are met.

## Verification steps

### 1. Run the test suite

```bash
python -m unittest discover -s tests -v
```

Expect all tests (existing + the new `tests/test_auth.py`) to pass.

### 2. Syntax / import check

Ensure [`server.py`](../server.py) compiles cleanly:

```bash
python -m py_compile server.py
```

(If the local environment lacks third-party deps, at minimum confirm no syntax
errors from `py_compile`.)

### 3. Manual guard sanity check (optional but recommended)

If a Python environment with `fastmcp` is available:

```python
import server
server.API_KEY = "test-token"
# Stub headers via direct reasoning or a mocked get_http_headers:
#   - validate "Bearer test-token" -> ""
#   - validate "X-Api-Key: test-token" -> ""
#   - validate missing/wrong        -> error string
```

### 4. Acceptance criteria walkthrough

Confirm each criterion from the parent plan:

- **Requests without a valid token are rejected** — every tool returns
  `ERROR: unauthorized - missing or invalid API token` before resolving credentials.
- **A valid token allows normal operation** — the guard returns `""` and the tool
  proceeds unchanged.
- **Auth configuration is documented** — README, `.env.example`, and
  `docker-compose.yaml` all reference `CALDAV_MCP_API_KEY` and the header forms.

### 5. Confirm scope was respected

- No behavior other than auth was changed.
- `CALDAV_MCP_API_KEY` unset => auth disabled (backward compatible).
- Bind address remains `0.0.0.0`; reverse-proxy/TLS guidance is in the README.

## Definition of done

- Tests pass; module compiles.
- All acceptance criteria verified.
- No unintended files changed.

## Constraints / rules

- This step is verification only — do NOT add new features, refactor, or expand
  scope.
- If verification reveals a defect, fix it within the pre-existing sub-plans' scope;
  do not invent new behavior.

## Commit

If any fix was required during verification, commit it with a short message, e.g.:

```text
Finalize MCP authentication
```
