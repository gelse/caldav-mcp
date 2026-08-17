# Plan: Update README security documentation

## Problem

The README correctly documents "No authentication on the MCP endpoint"
([`README.md:12`](../README.md:12)), but does not warn about the risk of public exposure, and it
does not yet reflect planned changes (auth token, timezone env, dependency pinning).

## Goal

Keep documentation accurate and add an explicit security guidance section.

## Steps

1. Add a "Security" section warning against exposing the endpoint publicly and recommending a
   reverse proxy / TLS.
2. Document the new `CALDAV_MCP_API_KEY`/auth header once issue #03 lands.
3. Document the timezone env var (issue #02) and any new dependency/version notes (issue #06).
4. Update the env config table ([`README.md:21`](../README.md:21)) to include all env vars.
5. Confirm the example `curl` call ([`README.md:67`](../README.md:67)) includes the auth header
   after issue #03.

## Affected files

- `README.md`

## Acceptance criteria

- README reflects current config surface and includes security guidance.
