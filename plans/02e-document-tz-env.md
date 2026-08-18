# Plan 02e: Document the `TZ` environment variable

## Context

This is **sub-step 02e** of the overall plan
[`02-timezone-utc-vs-local-fix.md`](./02-timezone-utc-vs-local-fix.md). It documents the `TZ`
environment variable that the server now uses (introduced in
[`02a-timezone-config-helpers.md`](./02a-timezone-config-helpers.md)) for "today"/"week" boundaries
and date-only inputs. This is a documentation-only sub-step; no Python code changes.

## Current state

- [`docker-compose.yaml`](../docker-compose.yaml:14) already sets `TZ: Europe/Vienna`, so no change
  is needed there (verify only).
- [`.env.example`](../.env.example) does not mention `TZ`.
- [`README.md`](../README.md:21) Config table lists only `CALDAV_MCP_PORT` and `CALDAV_MCP_PATH`.

## Change

1. Verify that [`docker-compose.yaml`](../docker-compose.yaml:14) already contains `TZ: Europe/Vienna`.
   If present, make **no** change. If missing, add it under the `environment` block as:

   ```yaml
       TZ: Europe/Vienna
   ```

2. In [`.env.example`](../.env.example), append a new line under the `# Server settings` section,
   after the existing `CALDAV_MCP_PORT` / `CALDAV_MCP_PATH` entries:

   ```
   # Timezone for "today"/"week" boundaries and date-only inputs (IANA name, e.g. Europe/Vienna)
   TZ=Europe/Vienna
   ```

3. In [`README.md`](../README.md), add a new row to the Config table (after the
   `CALDAV_MCP_PATH` row at [`README.md:24`](../README.md:24)):

   ```markdown
   | `TZ` | `UTC` | IANA timezone used for "today"/"week" boundaries and date-only inputs (e.g. `Europe/Vienna`) |
   ```

   Also update the surrounding prose if it currently implies only two config variables. Keep the
   table format consistent (pipe-aligned not required, but the column headers must remain
   `Env | Default | Description`).

## Definition of done

- [`docker-compose.yaml`](../docker-compose.yaml) sets `TZ` (already present, otherwise added).
- [`.env.example`](../.env.example) documents `TZ` under the server settings section.
- [`README.md`](../README.md) Config table includes a `TZ` row with default `UTC` and an accurate
  description.
- No Python code or behavior is changed in this sub-step.

## Constraints

- Do **not** modify [`server.py`](../server.py) or any test file in this sub-step.
- Do **not** introduce a new variable name such as `CALDAV_MCP_TZ`; document only `TZ`.
- Do not deviate from this plan.
