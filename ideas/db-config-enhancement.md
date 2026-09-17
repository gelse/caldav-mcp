# Plan: SQLite-Based Config (Simple / Pro Modes)

## Problem and Goal

The server currently takes CalDAV connection data from environment variables and per-request headers. This works for a single user with a single server, but not for multi-server setups or multiple users.

This plan adds an optional SQLite-based configuration store. It introduces two operating modes:

- **Simple** (default): current behavior, unchanged for users.
- **Pro** (opt-in via env var): the SQLite config is required. Users are registered in the store and bound to the configs they may access.

The server stays stateless. The SQLite store is read at startup and never written by the server. A separate CLI manages the store.

## Mode Concept

| | Simple (default) | Pro (`DB_CONFIG_ENABLED=true`) |
|---|---|---|
| Config source | Built-in singleton built from env vars | SQLite store, loaded at startup |
| MCP endpoint auth | Env API key (`CALDAV_MCP_API_KEY`) | DB users (username + hashed key); `CALDAV_MCP_API_KEY` is ignored |
| CalDAV access | Per-request headers or env fallback | Per-remote stored credentials, or passthrough headers |
| Tool addressing | Plain calendar names | Dotted paths `config.remote.calendar` |

Both modes run **identical code paths** after startup. The only difference is where the config comes from: simple mode is implemented internally as a single predefined read-only config singleton, loaded at startup, that mimics what the DB loader produces. The singleton is truly read-only after init; config changes take effect by restarting the container. No hot reload.

## Config Data Model (Conceptual)

- **Config**: identified by a unique name. Owns a list of remotes and a list of calendars per remote.
- **Remote**: one CalDAV server. Carries an auth mode:
  - **direct**: CalDAV credentials are stored in the config and required here.
  - **passthrough**: credentials come from each request.
- **Calendar**: a calendar exposed on a remote, addressable by name.
- **User**: a username plus a hashed API key. Holds the list of config names the user may access.

Conceptual resolution: `config.remote.calendar` — for example `work.nextcloud.team-meetings`.

## Auth Model

Two layers, as today, but their sources change in pro mode:

1. **MCP endpoint auth**: simple mode keeps the env API key. Pro mode replaces it with DB users; a request authenticates with a username and its key (stored hashed). `CALDAV_MCP_API_KEY` is ignored when pro mode is active.
2. **CalDAV credentials**: stored ("direct") credentials are encrypted at rest with a master key from the env var `CALDAV_MCP_CONFIG_SECRET`. The CLI encrypts on write; the server decrypts on load. The master key is a deployment requirement in pro mode. Passthrough credentials still arrive per request.

## Addressing Model

- **Simple mode**: tools accept plain calendar names, as today. Backward compatible.
- **Pro mode**:
  - Read tools (list/get/search/freebusy) **fan out**: they run across all calendars the user may access and aggregate results per remote.
  - Write tools (create/update/delete/move, attendee changes) **require an explicit identifier** — the dotted path — so writes are never ambiguous.

## Edge Cases

- **No config at all** (neither env nor DB): "header mode". The request headers `X-Caldav-Url`, `X-Caldav-Username`, `X-Caldav-Password` are required.
- **Any config given** (env or DB): the headers above are **ignored**. This is a behavior change: today headers take precedence over env config.
- **Unreachable remote / bad credentials**: reported per remote, independently. A user with several remotes gets partial-success reporting — one remote may fail while others return results.

## Passthrough Constraint

A user's accessible configs may contain **at most one passthrough remote in total**. With more than one, a single per-request credential set would be ambiguous. The config store's validation must enforce this constraint.

## Non-Goals

- No hot reload of config; restart to apply changes.
- No server-side writes to the SQLite store; only the CLI writes.
- No user self-service or password rotation flows beyond CLI operations.
- No changes to the MCP tool surface in simple mode.
- No per-calendar permissions; access control is per config.

## Milestones

Each milestone is independently deliverable and verifiable.

### M1 — Header-Precedence Change Request

Ship the new precedence rule in the current (header/env) system, before any DB work: when a config exists via env vars, request headers are ignored. This defines the semantics both modes will later share.

- Deliverable: header/env precedence follows the plan's edge-case rule; documented.
- Verification: unit tests covering all precedence combinations; docs updated.

### M2 — Config Singleton Abstraction (Simple Mode on Top)

Introduce a read-only, startup-loaded config singleton as the single source of connection and calendar data, and back simple mode with a built-in config derived from env vars. No behavior change beyond M1.

- Deliverable: all tools resolve calendars and credentials through the singleton; simple mode behaves as before.
- Verification: existing test suite passes unchanged; simple-mode regression tests for env-only and header mode.

### M3 — SQLite Store, Encryption, and CLI

Add the SQLite store with the conceptual data model above, credential encryption at rest (`CALDAV_MCP_CONFIG_SECRET`), the at-most-one-passthrough validation, and the standalone CLI entry point (`python -m caldav_mcp.config_cli`) for managing users, configs, remotes, and calendars. The server does not use the store yet.

- Deliverable: CLI can create and inspect a valid store; invalid stores (e.g. two passthrough remotes) are rejected.
- Verification: CLI round-trip tests; validation tests for the passthrough constraint; encryption round-trip (write via CLI, decrypt on read).

### M4 — Pro Mode: Auth and Tool Addressing

Enable pro mode via `DB_CONFIG_ENABLED`: the store is loaded into the same singleton abstraction from M2; DB users replace the env API key for MCP endpoint auth; read tools fan out across all accessible calendars with per-remote aggregation; write tools require dotted-path identifiers.

- Deliverable: pro mode works end to end against the store built by the CLI; simple mode unchanged.
- Verification: integration tests for DB-backed auth and fan-out reads; tests that writes without an explicit identifier are rejected; simple-mode suite still green.

### M5 — Partial-Failure Reporting and Docs

Implement per-remote, independent failure reporting across fanned-out reads (one remote failing must not mask others), cover remaining edge cases (header mode, headers-ignored rule in both modes), and update all documentation (architecture, API, env vars, deployment, CLI usage).

- Deliverable: partial-success results and docs complete.
- Verification: tests for mixed success/failure across remotes; docs review against implemented behavior.

## Risks / Open Decisions

- None currently. The header-precedence change in M1 is a deliberate behavior change and should be called out in release notes.
