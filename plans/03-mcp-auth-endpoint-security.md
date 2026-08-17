# Plan: Add authentication to the MCP endpoint

## Problem

The server binds `0.0.0.0` ([`server.py:489`](../server.py:489)) and the compose file publishes
`8600:8080` ([`docker-compose.yaml`](../docker-compose.yaml:9)). The MCP endpoint has no
authentication (documented at [`README.md:12`](../README.md:12)). Anyone who can reach the port can
read, create, modify, and delete calendar events using arbitrary `X-Caldav-*` headers.

## Goal

Require an API-token/bearer credential before accepting any MCP request, so only authorized
clients can use the server.

## Steps

1. Add a `CALDAV_MCP_API_KEY` (or `CALDAV_MCP_TOKEN`) env var; treat it as the shared secret.
2. Add a dependency/guard that verifies the `Authorization: Bearer <token>` header (or a custom
   `X-Api-Key` header) on every request before any tool executes.
3. Reject missing/incorrect tokens with a clear auth error.
4. Document the new env var and the `Authorization` header requirement in [`README.md`](../README.md:61)
   and [`.env.example`](../.env.example).
5. Update [`docker-compose.yaml`](../docker-compose.yaml:11) to pass the token, and consider binding
   to `127.0.0.1` or documenting reverse-proxy TLS termination if exposed publicly.

## Affected files

- `server.py` (add auth guard)
- `README.md`, `.env.example`, `docker-compose.yaml`

## Acceptance criteria

- Requests without a valid token are rejected with a clear error.
- A valid token allows normal operation.
- Auth configuration is documented.
