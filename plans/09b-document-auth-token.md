# Plan 09b — Document the API-key auth token and header

> Parent plan: [`09-readme-security-docs.md`](./09-readme-security-docs.md)

## Objective

Update [`README.md`](../README.md) to document the new `CALDAV_MCP_API_KEY` environment variable and
the corresponding auth header, once issue #03 (MCP endpoint auth) has landed.

## Context you must know

This sub-plan corresponds to step 2 of the parent plan and **depends on issue #03** (plan
[`03-mcp-auth-endpoint-security.md`](./03-mcp-auth-endpoint-security.md)). Do not author this
documentation unless plan 03 has been implemented.

Relevant facts to reflect once #03 is in place:

- The env var name is `CALDAV_MCP_API_KEY`.
- Sub-plan 03a introduces header-name constants and a `_require_auth()` guard. The auth header is
  `X-Mcp-Api-Key` (confirm the exact literal against plan 03a / the implemented `server.py`, and use
  that literal verbatim in the README).
- The guard rejects requests where the header does not match the configured key in constant time.

Reference lines that must stay accurate:

- The "No authentication on the MCP endpoint" bullet currently at [`README.md:12`](../README.md:12)
  in `## Design`.
- The env config table at [`README.md:21`](../README.md:21).
- The example `curl` call at [`README.md:83`](../README.md:83).

## Chosen mechanism (do not deviate)

- Replace the `## Design` bullet `No authentication on the MCP endpoint` with a bullet describing
  optional API-key authentication via the `X-Mcp-Api-Key` header (falling back to the
  `CALDAV_MCP_API_KEY` env var).
- Update the example `curl` call to include the auth header.

## Implementation steps

### Step 1 — Confirm the exact header name

Open [`server.py`](../server.py) (or plan `03a`/`03b`) and confirm the literal header name used for
the API key. Use **that** literal in the README; do not guess.

### Step 2 — Update the "Design" bullet

In [`README.md`](../README.md), change the `## Design` bullet

```markdown
- **No authentication** on the MCP endpoint.
```

to

```markdown
- **Optional API-key authentication** on the MCP endpoint via the `X-Mcp-Api-Key` header
  (overridden by the `CALDAV_MCP_API_KEY` environment variable). When unset, the endpoint is
  unauthenticated.
```

(Substitute the exact header name confirmed in Step 1.)

### Step 3 — Update the "Config" table

In the `## Config` table (currently [`README.md:21`](../README.md:21)), add a row:

```markdown
| `CALDAV_MCP_API_KEY` | _(unset)_ | API key required in the `X-Mcp-Api-Key` header to call tools |
```

### Step 4 — Update the example `curl` call

In [`README.md:83`](../README.md:83), add the auth header line after the other headers, e.g.:

```bash
  -H 'X-Mcp-Api-Key: your-api-key' \
```

### Step 5 — Verify

Re-read the README to confirm the header name, env var name, and table row are internally
consistent and match the implementation in `server.py`.

## Definition of done

- [ ] `## Design` bullet describes optional API-key auth with the correct header and env var names.
- [ ] `## Config` table includes a `CALDAV_MCP_API_KEY` row with an accurate description.
- [ ] Example `curl` call includes the auth header.
- [ ] All names match the implemented [`server.py`](../server.py) exactly.

## Constraints / rules

- Documentation only. Do not modify `server.py` or configuration files.
- Only proceed if plan 03 (auth) has been implemented; otherwise mark this sub-plan as blocked.

## Commit

```bash
git add README.md && git commit -m "Document API-key auth in README"
```
