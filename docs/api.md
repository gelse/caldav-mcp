# API Reference

## Authentication headers

All requests to `/mcp` require authentication when `CALDAV_MCP_API_KEY` is set (simple mode) or when `DB_CONFIG_ENABLED=true` (pro mode).

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization: Bearer <token>` | Yes | API key (simple mode) or DB user key (pro mode) |
| `X-Api-Key: <token>` | Alternative | Same as Bearer; either header is accepted |
| `X-Mcp-Username` | Pro mode only | DB username; required when `DB_CONFIG_ENABLED=true` |
| `X-Caldav-Url` | Header mode / passthrough | CalDAV server URL (required in header mode; used for passthrough remotes in pro mode; ignored in env mode) |
| `X-Caldav-Username` | Header mode / passthrough | CalDAV username (same semantics as `X-Caldav-Url`) |
| `X-Caldav-Password` | Header mode / passthrough | CalDAV password (same semantics as `X-Caldav-Url`) |

**Header semantics by mode:**

| Mode | `X-Caldav-*` behavior |
|------|----------------------|
| Env (`CALDAV_URL` set) | Ignored entirely |
| Header (`CALDAV_URL` unset) | Required on every request — all three headers |
| Pro (`DB_CONFIG_ENABLED=true`) | Used only for passthrough remotes; ignored for direct remotes |

`X-Caldav-Username` and `X-Caldav-Password` are reserved for a future passthrough mode and are ignored when `CALDAV_URL` is set.

## Addressing model

**Simple mode** (env / header): tools accept a plain `calendar_name` string (e.g. `"work"`). The calendar is looked up from the configured remote.

**Pro mode** (`DB_CONFIG_ENABLED=true`): write tools require a dotted-path identifier in the form `config.remote.calendar` (e.g. `"main.radicale.work"`). Plain names are rejected with an error. For parameterless read tools (e.g. `caldav_list_calendars`), an empty `calendar_name` fans out across all accessible remotes and calendars. A dotted path narrows the query to a single calendar.

Dotted paths have exactly three dot-separated segments. Config, remote, and calendar names must not contain dots (enforced by the store's charset regex). See [`docs/cli.md`](cli.md) for managing configs, remotes, and calendars.

## Aggregated read results (pro mode)

Read tools fan out across every accessible remote and return one entry per `(config, remote, calendar)` pair. Each entry is addressed by the dotted path components (`config_name`, `remote_name`, `calendar_name`) and carries either the tool's own payload under `data` or, on failure, an `error` string. The per-entry status vocabulary below is what the rendered `message` reports for each remote — it is not a separate JSON field on each entry.

### Per-entry status vocabulary

| Status | Meaning |
|--------|---------|
| `ok` | Success — data returned |
| `empty` | Success — no matching data (zero results) |
| `auth` | `AuthError` — missing or invalid CalDAV credentials |
| `not_found` | `NotFoundError` — calendar or event does not exist |
| `error` | Any other exception |

### Top-level status

- `status`: `ok` when at least one entry succeeded, `error` when all failed, `empty` for zero accessible calendars.
- `message`: human-readable per-remote summary (see rendered format below).

### Example: `caldav_get_events` with partial failure

```json
{
  "status": "ok",
  "message": "OK Fan-out across 3 scopes: 2 ok, 1 error\n- [ok] work.nextcloud: 2 calendars\n- [error] work.radicale: connection refused",
  "data": [
    {
      "config_name": "work",
      "remote_name": "nextcloud",
      "calendar_name": "meetings",
      "data": [
        {"uid": "evt-1@nextcloud", "summary": "Sprint planning", "dtstart": "2026-09-15T10:00:00", "dtend": "2026-09-15T11:00:00"}
      ]
    },
    {
      "config_name": "work",
      "remote_name": "nextcloud",
      "calendar_name": "personal",
      "data": [
        {"uid": "evt-2@nextcloud", "summary": "Dentist", "dtstart": "2026-09-16T14:00:00", "dtend": "2026-09-16T15:00:00"}
      ]
    },
    {
      "config_name": "work",
      "remote_name": "radicale",
      "calendar_name": "personal",
      "error": "connection refused"
    }
  ]
}
```

### Rendered message format

The `message` field is produced by `render_fanout_message` and always follows this structure:

1. **First line**: top-level tag (`OK` / `ERROR:[server]`) + scope count + ok/error counts
2. **Detail lines**: one `- [status] config.remote: detail` line per remote in declaration order

When a remote spans several calendars, entries are grouped into a single line with a count (e.g. `2 calendars`). On failure, the detail is the exception text. Mixed outcomes within one remote use severity-based status (highest: `error` > `auth` > `not_found`).

```
OK Fan-out across 3 scopes: 2 ok, 1 error
- [ok] work.nextcloud: 2 calendars
- [error] work.radicale: connection refused
```

For all-failed results, the first line uses the `ERROR:[server]` tag:

```
ERROR:[server] Fan-out across 2 scopes: 2 error
- [auth] work.nextcloud: unauthorized - missing credentials
- [error] work.radicale: connection refused
```

## Admin REST API

The admin REST API manages pro-mode **users** in the SQLite config store:
rotate a compromised key, create or delete a user, grant or revoke config
access. It speaks plain HTTP on the same listener as `/mcp`, under
`/admin/users…` and `/admin/configs`, authenticated with a dedicated
admin key. It is not part of the MCP tool surface (the 14 tools below are
unchanged), and it does not manage configs, remotes, or calendars — those
stay CLI-managed (see [`docs/cli.md`](cli.md)). The one config-related
route, `GET /admin/configs`, is read-only: it lists the store's config
names so operators can discover which configs a user may be granted
without reaching for the CLI.

> **Release-notes callout — deliberate behavior change:** the admin API is
> the first time the *server* writes the SQLite config store (until now
> only the CLI wrote it; all writes still go through the store module),
> and `PUT /admin/users/{username}/key` is the first
> built-in flow for rotating a *user* API key. Earlier statements such as
> "the server never writes the store" or "no key rotation" are superseded:
> the struck-through "no built-in flow to rotate … user API keys" sentence
> is kept in [`docs/cli.md`](cli.md#limitations--non-goals), and the
> "only the CLI writes" rule is marked as replaced in that file's
> opening note.

### Enabling the API

Both conditions must hold, or the routes do not exist:

1. Pro mode: `DB_CONFIG_ENABLED=true`.
2. `CALDAV_MCP_ADMIN_KEY` is set to a non-empty value.

Otherwise requests receive the framework's 404. In pro mode the startup
log then says `Admin API disabled: CALDAV_MCP_ADMIN_KEY is not set`; a key
set outside pro mode logs a warning that it is ignored. The guard in
each handler independently refuses with 404 as well, so a registration bug
still could not expose the routes.

Admin API mutations write directly into the store, so the store file must
be writable by the server.
Deployments that mount the store read-only should leave
`CALDAV_MCP_ADMIN_KEY` unset (admin API disabled) — see
[`docs/configuration.md`](configuration.md).

The built-in admin UI is served at `/ui` when `SHOW_UI=true` and consumes
exactly these endpoints from the same origin; with the API disabled it
renders its “admin API not available” screen (see [`docs/ui.md`](ui.md#hosting)).

### Transport warning — read before sending the key

Send the admin key over **TLS or localhost only**. It travels in a request
header on every call; without TLS it crosses the network in plaintext.
Keep the listener private: bind the published port to localhost (e.g.
`127.0.0.1:8600:8080` in compose), restrict it at the firewall/VPN layer,
or enable TLS via `CALDAV_MCP_TLS_CERT` / `CALDAV_MCP_TLS_KEY`.

**`X-Forwarded-For`:** the admin rate limiter buckets failures by the
first hop of `X-Forwarded-For` when that header is present, otherwise by
the socket peer address. Only trust the header behind a reverse proxy that
*sanitizes* it — i.e. overwrites any client-supplied value, e.g.
`proxy_set_header X-Forwarded-For $remote_addr;`. If clients can reach the
server directly, or the proxy merely appends to a client-supplied chain,
callers choose their own first hop and the per-IP rate limit becomes
advisory (a determined attacker rotates through fake IPs).

### Admin key authentication

Present the key on every admin request, in either header:

| Header | Example |
|--------|---------|
| `Authorization: Bearer <key>` | `Authorization: Bearer <admin-key>` |
| `X-Api-Key: <key>` | `X-Api-Key: <admin-key>` |

The comparison is constant-time. A missing or wrong key returns 401
(`{"error": "unauthorized - missing or invalid credentials"}`) and counts
as a failed attempt against the rate limit. The admin key is independent
of `CALDAV_MCP_API_KEY` (simple-mode endpoint key) and of per-user DB keys
(pro-mode MCP auth).

Key hygiene is strict: plaintext keys are accepted **only in JSON request
bodies** — never in a query string, which proxy logs would capture — and
are never stored, logged, echoed, or returned. Responses carry neither
keys nor key hashes; a user object is always
`{"username": …, "configs": […]}`.

### Restart and reload: the one rule

**User changes made through this API apply live; topology changes still
require a restart.** After every successful mutation the server re-reads
the user rows and swaps its in-process user snapshot, so the change takes
effect on the next MCP request. Create, delete, key rotation, grant,
revoke, and replace all behave this way — none of their responses carry a
`restart_required` field.

Two exceptions, both signaled in the response body:

- **Failed live reload** — if the snapshot refresh fails, the write has
  already succeeded: the response carries `"reload": "failed"` and a
  server restart applies the change as the fallback.
- **Configs created after startup** — the config/remote/calendar topology
  is frozen when the server starts. Granting or replacing grants for a
  config the running server does not know yet responds with
  `"config_topology_restart_required": true`: the grant is stored and the
  user's access updates live, but reaching that config needs a restart.
  Revoke never carries the flag — revoking never needs topology.

CLI-made changes (configs, remotes, calendars — and users) reach a running
server at its next restart; for users that is the dependable path, though a
later successful admin-API mutation also re-reads the whole user table and
may pick such changes up sooner. See [`docs/cli.md`](cli.md) and
[`docs/pro-mode.md`](pro-mode.md). Everything below references this rule
instead of repeating it.

### Routes

Request bodies are JSON with `Content-Type: application/json`. The
`Errors` column lists the route-specific statuses; 401, 429, 503 (and 500
for unexpected faults) can occur on any route and are described under
[Status codes and error bodies](#status-codes-and-error-bodies).

| Method & path | Body | Success | Errors |
|---|---|---|---|
| `GET /admin/users` | – | 200 `{"users": [user…]}` (store insertion order) | 401, 429, 503 |
| `GET /admin/users/{username}` | – | 200 user | 400, 401, 404, 429, 503 |
| `GET /admin/configs` | – | 200 `{"configs": [name…]}` (store insertion order) | 401, 429, 503 |
| `POST /admin/users` | `{"username": str, "key": str}` | **201** user | 400, 401, 409, 429, 503 |
| `DELETE /admin/users/{username}` | – | 200 `{"deleted": true, "username": …}` (grants cascade with the user) | 400, 401, 404, 429, 503 |
| `PUT /admin/users/{username}/key` | `{"key": str}` | 200 `{"username": …, "rotated": true}` | 400, 401, 404, 429, 503 |
| `POST /admin/users/{username}/configs` | `{"config": str}` | 200 user (idempotent — re-granting is a no-op) | 400, 401, 404, 429, 503 |
| `PUT /admin/users/{username}/configs` | `{"configs": [str, …]}` | 200 user (replace-all; duplicate names are deduplicated) | 400, 401, 404, 409, 429, 503 |
| `DELETE /admin/users/{username}/configs/{config}` | – | 200 `{"revoked": true, …}` (revoking a never-granted config is a no-op) | 400, 401, 404, 429, 503 |

Grant and replace responses may additionally carry
`config_topology_restart_required: true`, and any mutation may carry
`"reload": "failed"` — both per [Restart and reload: the one
rule](#restart-and-reload-the-one-rule). `404` on a grant means the user
**or** the config is unknown; on revoke it means the user is unknown
(the pre-check), and on `PUT …/configs` it names either the missing
configs or the unknown user.

### Examples

Placeholder secrets (`<admin-key>`, `<user-key>`, `<new-user-key>`) stand
in for real keys — substitute your own. With the bundled compose file the
listener is `http://localhost:8600`.

**Rotate a compromised key** — the new key authenticates on the next MCP
request and the old one stops (per [the one rule](#restart-and-reload-the-one-rule)):

```bash
curl -s -X PUT http://localhost:8600/admin/users/alice/key \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"key": "<new-user-key>"}'
# {"username": "alice", "rotated": true}
```

**Create a user:**

```bash
curl -s -X POST http://localhost:8600/admin/users \
  -H "X-Api-Key: <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"username": "alice", "key": "<user-key>"}'
# 201  {"username": "alice", "configs": []}
```

**List and read users** (no request body):

```bash
curl -s http://localhost:8600/admin/users \
  -H "Authorization: Bearer <admin-key>"
# {"users": [{"username": "alice", "configs": ["work"]}]}

curl -s http://localhost:8600/admin/users/alice \
  -H "Authorization: Bearer <admin-key>"
# {"username": "alice", "configs": ["work"]}
```

**Grant, replace, and revoke config access:**

```bash
# Grant one config (idempotent)
curl -s -X POST http://localhost:8600/admin/users/alice/configs \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"config": "work"}'
# {"username": "alice", "configs": ["work"]}

# Replace the full grant set
curl -s -X PUT http://localhost:8600/admin/users/alice/configs \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"configs": ["work", "personal"]}'
# {"username": "alice", "configs": ["work", "personal"]}

# Revoke one config (no-op if never granted)
curl -s -X DELETE http://localhost:8600/admin/users/alice/configs/work \
  -H "Authorization: Bearer <admin-key>"
# {"revoked": true, "username": "alice", "config": "work"}
```

**Delete a user** (their grants are removed with them):

```bash
curl -s -X DELETE http://localhost:8600/admin/users/alice \
  -H "Authorization: Bearer <admin-key>"
# {"deleted": true, "username": "alice"}
```

**Errors** are always a JSON object with an `error` string:

```bash
curl -s http://localhost:8600/admin/users/ghost \
  -H "Authorization: Bearer <admin-key>"
# {"error": "user not found: 'ghost'"}
```

### Status codes and error bodies

Responses generated by the API are JSON. Errors are always
`{"error": "<message>"}` — the HTTP equivalent of the CLI's
`error: <message>` output. Successes are never empty: there is no 204
anywhere — every mutation answers with a JSON body that carries the
reload signals described in [Restart and reload: the one
rule](#restart-and-reload-the-one-rule) whenever they apply.

| Status | Meaning |
|--------|---------|
| `200` | Success — reads and all mutations except user creation |
| `201` | User created (`POST /admin/users`) |
| `400` | Invalid input: malformed or non-object JSON body, wrong `Content-Type`, missing or non-string field, empty field (`username`, `key`, `config`), a rotation key that is not valid UTF-8, or a path username failing the transport guard |
| `401` | Missing or invalid admin key |
| `403` | Reserved — unused (the API has no roles) |
| `404` | Unknown user or config; when the API is disabled the route was never registered and the framework serves its own 404 |
| `405` | Wrong HTTP method for a routed path (framework) |
| `409` | Conflict: duplicate user on `POST /admin/users`, or a foreign-key race on `PUT …/configs` |
| `429` | Rate limited — `Retry-After` header set (see below) |
| `500` | Unexpected server error — generic message; the detail is logged server-side only |
| `503` | Store unavailable: locked or unreadable (message carries a retry hint) or schema newer than the running code |

Store errors funnel through one mapping: validation → 400, not found →
404, conflict → 409, store/schema/lock failures → 503. The store owns all
domain validation — non-empty username, config names without dots, at most
one passthrough remote per user — so a grant or replace that would give a
user reach to two passthrough remotes fails with 400 and a message naming
the offending user(s).

**Path-username transport guard:** store usernames are free-form, but in a
URL path a username longer than 128 characters or containing control
characters (NUL included) is rejected with 400, and one containing `/`
cannot be addressed at all (no route matches → 404). Such names can slip
in from either side — the CLI accepts them, and `POST /admin/users` does
not apply the path guard to body usernames — but every user remains
visible in `GET /admin/users` and manageable through
[`caldav-mcp-config user …`](cli.md#user); the API just cannot address
those names by path.

### Rate limiting

The admin API has its **own** limiter, separate from MCP-endpoint auth:
failed admin-key guesses cannot lock MCP clients out, and vice versa
(different secrets, independent failure buckets). It uses the same knobs —
`CALDAV_MCP_RATE_LIMIT_MAX_FAILURES` (default `10`) failures per
`CALDAV_MCP_RATE_LIMIT_WINDOW_SECONDS` (default `60`) seconds — keyed per
client IP as described in the transport warning. A successful
authentication clears that IP's failure history.

Once limited, every route answers 429:

```json
{"error": "rate limited - too many failed attempts, retry in 5s"}
```

with a `Retry-After: <seconds>` header. In practice the value is 5
seconds: a request rejected while limited is not recorded as a new
failure, so the count stays at the threshold (the backoff formula —
doubling per extra recorded failure, capped at 5 minutes — would need a
higher count to grow). The block lifts once failures age out of the
sliding window; the limiter is in-memory and resets on server restart.

## MCP Tools

All tools are accessible via the Streamable HTTP endpoint at `/mcp`.

> **Read-only mode:** When `CALDAV_MCP_READ_ONLY=true`, only 8 read-only
> tools (the 7 query tools plus `caldav_list_attendees`) below are registered.  Write tools (`caldav_create_event`,
> `caldav_update_event`, `caldav_delete_event`, `caldav_move_event`,
> `caldav_add_attendee`, `caldav_remove_attendee`) are hidden and cannot be
> called via MCP.

### caldav_list_calendars

List all calendars available for the configured account.

**Parameters:** None

**Returns:** `ToolResult` with `data` = list of `{name, url}` dicts.

### caldav_get_events

Get events in a date range for a calendar.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `calendar_name` | str | `""` | Calendar name (defaults to first) |
| `start` | str | `""` | Start datetime (ISO 8601; defaults to today 00:00) |
| `end` | str | `""` | End datetime (defaults to start + 1 day) |

### caldav_get_today_events

Get events for today (00:00 to 24:00).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `calendar_name` | str | `""` | Calendar name |

### caldav_get_week_events

Get events for the next 7 days.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `calendar_name` | str | `""` | Calendar name |

### caldav_get_event_by_uid

Get a specific event by its UID (includes attendees).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | str | *(required)* | Event UID |
| `calendar_name` | str | `""` | Calendar name |

### caldav_create_event

> **Not available in read-only mode.**

Create a new calendar event.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `summary` | str | *(required)* | Event title |
| `start` | str | *(required)* | Start datetime (ISO 8601) |
| `end` | str | `""` | End datetime (defaults to start + 1 hour) |
| `calendar_name` | str | `""` | Calendar name |
| `location` | str | `""` | Event location |
| `description` | str | `""` | Event description |
| `categories` | str | `""` | Comma-separated categories |
| `priority` | str | `""` | Priority 0-9 |
| `rrule` | str | `""` | RRULE string (RFC 5545) |
| `attendees` | str | `""` | Comma-separated email addresses |

### caldav_update_event

> **Not available in read-only mode.**

Update an existing event by UID. Only provided fields are updated.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | str | *(required)* | Event UID |
| `summary` | str | `""` | New summary |
| `start` | str | `""` | New start datetime |
| `end` | str | `""` | New end datetime |
| `calendar_name` | str | `""` | Calendar name |
| `location` | str | `""` | New location |
| `description` | str | `""` | New description |

### caldav_add_attendee

> **Not available in read-only mode.**

Add an attendee to an existing event.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | str | *(required)* | Event UID |
| `email` | str | *(required)* | Attendee email |
| `calendar_name` | str | `""` | Calendar name |
| `role` | str | `"REQ-PARTICIPANT"` | RFC 5545 ROLE |

### caldav_remove_attendee

> **Not available in read-only mode.**

Remove an attendee from an existing event.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | str | *(required)* | Event UID |
| `email` | str | *(required)* | Attendee email |
| `calendar_name` | str | `""` | Calendar name |

### caldav_list_attendees

List attendees of an event.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | str | *(required)* | Event UID |
| `calendar_name` | str | `""` | Calendar name |

### caldav_move_event

> **Not available in read-only mode.**

Move an event to another calendar (copy with new UID, delete original).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | str | *(required)* | Event UID |
| `target_calendar` | str | *(required)* | Destination calendar name |
| `source_calendar` | str | `""` | Source calendar (defaults to first) |

### caldav_delete_event

> **Not available in read-only mode.**

Delete an event by UID.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `uid` | str | *(required)* | Event UID |
| `calendar_name` | str | `""` | Calendar name |

### caldav_search_events

Search events by text (summary/description/location).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | str | *(required)* | Search text |
| `calendar_name` | str | `""` | Calendar name |

### caldav_get_freebusy

Get free/busy information for a time range.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `start` | str | `""` | Start datetime (defaults to today 00:00) |
| `end` | str | `""` | End datetime (defaults to start + 1 day) |
| `calendar_name` | str | `""` | Calendar name |

## ToolResult

Every tool returns a `ToolResult` dataclass:

| Field | Type | Description |
|-------|------|-------------|
| `status` | `Status` | `ok`, `empty`, `auth`, `not_found`, or `error` |
| `message` | `str` | Human-readable text |
| `data` | `Any` | Optional structured payload |
