# Plan 03e — Add `CALDAV_MCP_API_KEY` to `.env.example`

> Parent plan: [`03-mcp-auth-endpoint-security.md`](03-mcp-auth-endpoint-security.md)
> Prerequisites: plans 03a/03b (code) and 03d (README). This step only updates the
> example env file.
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Document the new `CALDAV_MCP_API_KEY` environment variable in
[`.env.example`](../.env.example).

## Context you must know

Current [` .env.example`](../.env.example) content:

```ini
# CalDAV connection settings (provided as HTTP headers, env is fallback)
CALDAV_URL=https://cloud.gelse.net/remote.php/dav/calendars/werner/
CALDAV_USERNAME=werner
CALDAV_PASSWORD=CHANGE_ME

# Server settings
CALDAV_MCP_PORT=8080
CALDAV_MCP_PATH=/mcp
```

The token env var is `CALDAV_MCP_API_KEY`. It is a shared secret presented by clients
via either `Authorization: Bearer <token>` or `X-Api-Key: <token>`. When unset, auth
is disabled.

## Change to make

Add a new line under the `# Server settings` section (after `CALDAV_MCP_PATH`), plus
keep it clearly commented. For example:

```ini
# Server settings
CALDAV_MCP_PORT=8080
CALDAV_MCP_PATH=/mcp
# Shared secret token. Clients send it as `Authorization: Bearer <token>`
# or `X-Api-Key: <token>`. When unset, authentication is disabled.
CALDAV_MCP_API_KEY=CHANGE_ME
```

## Definition of done

- [` .env.example`](../.env.example) includes `CALDAV_MCP_API_KEY` with a `CHANGE_ME`
  placeholder and a brief comment explaining its purpose and accepted headers.
- No other variables are changed or removed.
- Only [` .env.example`](../.env.example) is modified.

## Constraints / rules

- Do NOT put a real secret in the example file; use `CHANGE_ME`.
- Keep the existing `CALDAV_URL/USERNAME/PASSWORD` entries untouched.
- Do NOT modify code, tests, README, or compose in this step.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Document API key in env example
```
