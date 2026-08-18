# Plan 03f — Pass the API token in `docker-compose.yaml`

> Parent plan: [`03-mcp-auth-endpoint-security.md`](03-mcp-auth-endpoint-security.md)
> Prerequisites: plans 03a/03b (code), 03d/03e (docs). This step only updates the
> compose file.
> This is a single atomic sub-step. Implement ONLY what is described here.

## Objective

Make the Docker Compose deployment pass the `CALDAV_MCP_API_KEY` token to the
container so the auth guard is actually enforced in production.

## Context you must know

Current [`docker-compose.yaml`](../docker-compose.yaml) `environment:` block
([`docker-compose.yaml:11-14`](../docker-compose.yaml:11)):

```yaml
    environment:
      CALDAV_MCP_PORT: "8080"
      CALDAV_MCP_PATH: "/mcp"
      TZ: Europe/Vienna
```

The server reads `CALDAV_MCP_API_KEY` from the environment. To make this usable
without hardcoding a secret in the repo, reference an environment variable from the
host (or a `.env` file) via `${...}` substitution.

## Change to make

Add a line to the `environment:` block. Recommended form (secret supplied externally
via a `.env` file or host env):

```yaml
    environment:
      CALDAV_MCP_PORT: "8080"
      CALDAV_MCP_PATH: "/mcp"
      CALDAV_MCP_API_KEY: "${CALDAV_MCP_API_KEY:-}"
      TZ: Europe/Vienna
```

- Use `${CALDAV_MCP_API_KEY:-}` so that when the variable is unset the value is empty
  (auth disabled), matching the server's backward-compatible behavior.
- Do NOT change the `ports` mapping (`"8600:8080"`) and do NOT change the bind to
  `127.0.0.1` (per the agreed decision: keep `0.0.0.0` and document reverse-proxy TLS
  in the README, which is handled in plan 03d).

Optionally add an inline comment above the new line:

```yaml
      # Shared API token; set in a local .env file or host environment.
```

## Definition of done

- [`docker-compose.yaml`](../docker-compose.yaml) `environment:` block includes
  `CALDAV_MCP_API_KEY: "${CALDAV_MCP_API_KEY:-}"`.
- The `ports`, `healthcheck`, and `TZ` entries remain unchanged.
- Only [`docker-compose.yaml`](../docker-compose.yaml) is modified.

## Constraints / rules

- Do NOT commit a real secret value into the compose file.
- Do NOT change the bind address or port mapping.
- Do NOT modify code, tests, README, or `.env.example` in this step.

## Commit

When done and verified, commit with a short message, e.g.:

```text
Pass API key through docker compose
```
