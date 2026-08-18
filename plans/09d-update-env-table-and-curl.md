# Plan 09d — Finalize README env table and example call

> Parent plan: [`09-readme-security-docs.md`](./09-readme-security-docs.md)

## Objective

Final pass over [`README.md`](../README.md) to ensure the env config table lists **all** environment
variables and the example `curl` call reflects the current endpoint behavior (including any auth
header). This covers steps 4 and 5 of the parent plan and consolidates preceding sub-plans.

## Context you must know

This sub-plan should run **after** 09a, 09b, and 09c. Its purpose is a verification + completeness
pass, not the introduction of new content. The env config table lives at [`README.md:21`](../README.md:21)
and the example `curl` call at [`README.md:83`](../README.md:83).

The full env surface of the project (server + deployment) is:

- `CALDAV_URL`, `CALDAV_USERNAME`, `CALDAV_PASSWORD` — CalDAV credentials (env fallback).
- `CALDAV_MCP_PORT` (default `8080`) and `CALDAV_MCP_PATH` (default `/mcp`).
- `CALDAV_MCP_API_KEY` (from issue #03, if implemented).
- The timezone env var (`CALDAV_TZ` or equivalent, from issue #02, if implemented).

## Chosen mechanism (do not deviate)

- Reconcile the `## Config` table with the env vars documented elsewhere in the README (the `## Design`
  section's env-fallback list and the Docker Compose `TZ` example if present).
- Ensure the example `curl` call includes the CalDAV credential headers and (if #03 landed) the auth
  header. Do not add unrelated headers.

## Implementation steps

### Step 1 — Inventory env vars

List every environment variable referenced across [`README.md`](../README.md), [`server.py`](../server.py),
[`.env.example`](../.env.example), and [`docker-compose.yaml`](../docker-compose.yaml). Note which are
server-side (read by `server.py`) vs. deployment-side (Compose `TZ`).

### Step 2 — Reconcile the "Config" table

In [`README.md:21`](../README.md:21), ensure there is a row for each server-side env var:

```markdown
| `CALDAV_MCP_PORT` | `8080` | Listen port (inside container) |
| `CALDAV_MCP_PATH` | `/mcp` | Streamable HTTP path |
| `CALDAV_MCP_API_KEY` | _(unset)_ | API key required in the `X-Mcp-Api-Key` header |
| `CALDAV_TZ` | `UTC` | Timezone for date-only input / day boundaries |
```

Include rows only for env vars that actually exist in the implementation.

### Step 3 — Verify the example `curl` call

At [`README.md:83`](../README.md:83), confirm the call includes:

- the CalDAV credential headers (`X-Caldav-Url`, `X-Caldav-Username`, `X-Caldav-Password`),
- the auth header (`X-Mcp-Api-Key`) if #03 is implemented.

### Step 4 — Final review

Read the README top-to-bottom and confirm the config surface, endpoint description, and example call
are mutually consistent and match the implementation.

## Definition of done

- [ ] `## Config` table lists every server-side env var with accurate defaults.
- [ ] Example `curl` call is correct and includes the auth header if applicable.
- [ ] No stale or contradictory statements remain in the README.

## Constraints / rules

- Documentation only; do not modify `server.py`, `.env.example`, `docker-compose.yaml`, or any plan
  files.
- Do not invent env vars that do not exist in the code.

## Commit

```bash
git add README.md && git commit -m "Finalize README config table and example call"
```
