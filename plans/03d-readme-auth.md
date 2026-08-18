# Plan 03d — Update README.md for authentication

> Parent plan: [`03-mcp-auth-endpoint-security.md`](03-mcp-auth-endpoint-security.md)
> Prerequisites: plans 03a/03b implement the guard; 03c adds tests. This step only
> documents the change.
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Document the new `CALDAV_MCP_API_KEY` env var, the required `Authorization` /
`X-Api-Key` header, and reverse-proxy TLS guidance in [`README.md`](../README.md).

## Context you must know

- Current relevant README lines:
  - [`README.md:11-17`](../README.md:11) — the "Design" bullet list explicitly says
    "**No authentication** on the MCP endpoint." This must change.
  - [`README.md:21-24`](../README.md:21) — the "Config" table lists env vars.
  - [`README.md:82-89`](../README.md:82) — the "Example call" `curl` block.
- The token env var is **`CALDAV_MCP_API_KEY`**.
- Two request-header forms are accepted: `Authorization: Bearer <token>` and
  `X-Api-Key: <token>`.
- Authentication is **enabled only when** `CALDAV_MCP_API_KEY` is set to a non-empty
  value. When unset, the server runs with auth disabled (backward-compatible dev mode).

## Changes to make (exact locations)

### 1. "Design" section ([`README.md:11-17`](../README.md:11))

- Replace the line `- **No authentication** on the MCP endpoint.` with a bullet
  describing the API-key/bearer auth, e.g.:

  ```md
  - **Token authentication** on the MCP endpoint: requests must present a valid
    `Authorization: Bearer <token>` or `X-Api-Key: <token>` header matching the
    `CALDAV_MCP_API_KEY` environment variable. Auth is disabled when that variable
    is unset.
  ```

### 2. "Config" table ([`README.md:21-24`](../README.md:21))

Add a row for the new env var, inserted in a sensible position (e.g. after the
`CALDAV_MCP_PATH` row):

```md
| `CALDAV_MCP_API_KEY` | *(none)* | Shared secret API token. When set, requests must include a matching `Authorization: Bearer <token>` or `X-Api-Key: <token>` header. |
```

### 3. "Example call" ([`README.md:82-89`](../README.md:82))

Update the `curl` example to include an auth header. Add an `Authorization` header
line (and, if you prefer, note the `X-Api-Key` alternative). Example addition after
the `Content-Type` line:

```bash
  -H 'Authorization: Bearer CHANGE_ME' \
```

Optionally add a short note above or below the snippet:

```md
Replace `CHANGE_ME` with the value of `CALDAV_MCP_API_KEY`. You may use
`-H 'X-Api-Key: CHANGE_ME'` as an alternative.
```

### 4. Reverse-proxy / exposure note

Add a short subsection (e.g. after "Docker Compose" or in the "MCP client" section)
stating that if the service is exposed beyond the local network, terminate TLS at a
reverse proxy (e.g. Caddy, nginx, Traefik) and keep the token secret. Example:

```md
## Security note

The server binds `0.0.0.0` and is published via the Docker Compose port mapping.
When exposing it beyond `localhost`, place it behind a reverse proxy that
terminates TLS (HTTPS) so the API token is not transmitted in cleartext. Always set
a strong `CALDAV_MCP_API_KEY`.
```

## Definition of done

- The "No authentication" statement is removed/replaced.
- The Config table documents `CALDAV_MCP_API_KEY`.
- The example `curl` call shows how to authenticate.
- A security note documents reverse-proxy/TLS guidance.
- Only [`README.md`](../README.md) is modified.

## Constraints / rules

- Do NOT implement code, config, or test changes here.
- Do NOT remove the existing CalDAV credential header documentation.
- Match existing Markdown style (tables, code fences, bold labels).
- Keep the token named exactly `CALDAV_MCP_API_KEY` and the header forms as
  `Authorization: Bearer <token>` and `X-Api-Key: <token>`.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Document MCP authentication in README
```
