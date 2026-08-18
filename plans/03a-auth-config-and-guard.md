# Plan 03a — Add auth config constant and `_require_auth()` guard helper

> Parent plan: [`03-mcp-auth-endpoint-security.md`](03-mcp-auth-endpoint-security.md)
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Introduce a shared secret environment variable and a reusable authentication guard
helper in [`server.py`](../server.py). No tool is wired to the guard yet — that is done
in plan 03b.

## Context you must know

- The server uses **FastMCP >= 3.4.0** (Streamable HTTP). It already imports
  [`get_http_headers`](../server.py:10) from [`fastmcp.server.dependencies`](../server.py:10),
  which returns request headers as a lowercase-keyed dict.
- Header keys from `get_http_headers()` are **lowercase**. The `Authorization`
  header is therefore accessible as `headers.get("authorization")`, and the
  `X-Api-Key` header as `headers.get("x-api-key")`.
- The currently-defined header-name constants live at [`server.py:15-17`](../server.py:15).
- The token is the shared secret, configured once via environment variable and
  compared to the request header value.

## Chosen mechanism (do not deviate)

- Env var name: **`CALDAV_MCP_API_KEY`**.
- Two accepted header forms (either is valid):
  1. `Authorization: Bearer <token>`
  2. `X-Api-Key: <token>`
- A request is authorized if **either** header carries a token that exactly equals
  the configured `CALDAV_MCP_API_KEY` (constant-time comparison preferred).
- If `CALDAV_MCP_API_KEY` is unset/empty, authentication is **disabled** (the guard
  is a no-op returning success). This preserves local-dev behavior until the env
  var is configured.

## Implementation steps

### Step 1 — Add header-name constants

In [`server.py`](../server.py:15), after the existing `HDR_*` constants, add:

```python
HDR_AUTHORIZATION = "authorization"
HDR_API_KEY = "x-api-key"
```

### Step 2 — Add the API-key env var constant

Near the other module-level env-derived constants ([`server.py:12-13`](../server.py:12)),
add:

```python
API_KEY = os.environ.get("CALDAV_MCP_API_KEY", "")
```

Note: keep it a plain module-level string read at import time, consistent with how
`DEFAULT_PORT` and `DEFAULT_PATH` are already read. Do not re-read it per request.

### Step 3 — Add the `_require_auth()` helper

Add a new function (place it just above `_resolve_credentials` at
[`server.py:35`](../server.py:35)):

```python
def _require_auth() -> str:
    """Enforce the shared API token, if configured.

    Returns an empty string on success, or an auth error string to return to the
    client when authentication fails. Authentication is disabled (returns "") when
    CALDAV_MCP_API_KEY is not set.
    """
    expected = API_KEY
    if not expected:
        return ""

    headers = get_http_headers()
    provided = ""
    auth = headers.get(HDR_AUTHORIZATION, "")
    if auth:
        scheme, _, token = auth.partition(" ")
        if scheme.lower() == "bearer":
            provided = token.strip()
    if not provided:
        provided = headers.get(HDR_API_KEY, "").strip()

    if provided and _const_eq(provided, expected):
        return ""
    return "ERROR: unauthorized - missing or invalid API token"
```

### Step 4 — Add the constant-time comparison helper

Add a small helper (place it directly above `_require_auth`):

```python
def _const_eq(a: str, b: str) -> bool:
    """Constant-time string comparison to avoid timing attacks on the token."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0
```

Use this helper instead of `==` when comparing the token. (If you prefer
`hmac.compare_digest`, import `hmac` at the top of the file and use
`hmac.compare_digest(provided, expected)` — functionality is equivalent. Choose one
and use it consistently; do not mix.)

### Step 5 — Do NOT wire it yet

Leave all existing `@mcp.tool()` functions unchanged in this step. Wiring happens
in plan 03b.

## Definition of done

- [`server.py`](../server.py) contains:
  - `HDR_AUTHORIZATION` and `HDR_API_KEY` constants.
  - `API_KEY` module constant read from `CALDAV_MCP_API_KEY`.
  - `_const_eq(a, b)` helper (or equivalent `hmac.compare_digest` usage).
  - `_require_auth()` helper that returns `""` when configured and valid,
    `""` when unconfigured, and an `ERROR: unauthorized ...` string otherwise.
- No tool function is modified.
- No other files are modified.
- The module still imports cleanly (no syntax errors).

## Constraints / rules

- Do NOT modify any tool function, any other helper, or any file beyond
  [`server.py`](../server.py).
- Do NOT add tests in this step.
- Do NOT update documentation in this step.
- Do NOT change the bind address, transport, or path.
- Follow existing code style: `%`-formatting, lowercase header constant names used
  as `headers.get(...)`, `ERROR:` prefix for error strings.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Add auth guard helper and API key config
```
