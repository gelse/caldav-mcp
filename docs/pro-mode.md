# Pro Mode

Pro mode (`DB_CONFIG_ENABLED=true`) loads configuration and users from the
SQLite config store at startup, enabling multi-user, multi-account deployments.

## Key differences from simple mode

- **DB-user auth**: Clients must send `X-Mcp-Username: <username>` plus
  `Authorization: Bearer <key>` (or `X-Api-Key`). The `CALDAV_MCP_API_KEY`
  env var is **ignored** — only DB-stored user credentials are accepted.
- **Fan-out reads**: Parameterless read tools (e.g. `caldav_list_calendars`,
  `caldav_get_events`) fan out sequentially across all accessible remotes
  and aggregate results per-remote. One remote's failure never masks others'
  results.
- **Partial-failure reporting**: Each scope produces an entry with a status
  (`ok`, `empty`, `auth`, `error`, `not_found`). The top-level result is `OK`
  when at least one scope succeeds, `ERROR` when all fail, and `EMPTY` when
  there are zero accessible calendars.
- **Dotted-path writes**: Write tools require a `config.remote.calendar`
  dotted path (e.g. `main.radicale.work`) to uniquely identify the target
  calendar. Plain calendar names are rejected.
- **Users apply live, other config changes still need a restart**: The
  store is read once at startup and frozen into an immutable `AppConfig`.
  User changes made through the [admin REST API](api.md#admin-rest-api)
  (create, delete, key rotation, grant, revoke, replace grants) take
  effect on the next request instead — their responses no longer carry
  `restart_required` (removed with M5 users-only hot reload); a failed
  live reload is reported as `"reload": "failed"`. Calendars provisioned
  through [`POST /user/calendars`](#user-managed-calendars-homelab) also
  apply live: the route re-reads the store and swaps the loaded topology
  in place, so the returned path works immediately. One caveat: granting
  a config that was added after startup responds with
  `config_topology_restart_required: true` — the grant applies, but
  reaching that config still needs a restart. Config, remote, and
  calendar changes made any other way (CLI, direct store edits) require a
  server restart.

## User-managed calendars (homelab)

`POST /user/calendars` lets a DB user add their own CalDAV calendar in one
authenticated call. The route builds the whole chain — personal config
`user-<username>`, a `direct` remote whose password is encrypted with that
user's per-user store secret, the calendar entry, and the grant — applies
it live, and returns the dotted `config.remote.calendar` path for
immediate use with the MCP tools. No restart follows the call; the full
contract (request fields, status codes, error bodies) is in
[User calendar provisioning](api.md#user-calendar-provisioning).

```bash
curl -s -X POST http://localhost:8600/user/calendars \
  -H "X-Mcp-Username: alice" \
  -H "Authorization: Bearer <user-key>" \
  -H "Content-Type: application/json" \
  -d '{"caldav_url": "http://radicale:5232", "caldav_username": "bob",
       "caldav_password": "<caldav-password>", "calendar_name": "hobby"}'
# 201  {"path": "user-alice.hobby.hobby", "config": "user-alice",
#       "remote": "hobby", "calendar": "hobby"}
```

**Naming rules.**

- The personal config is always `user-<username>` with every character
  outside `[A-Za-z0-9_-]` replaced by `-` — derived from the
  authenticated username, never from the request body.
- `calendar_name` (and the optional `remote_name`, which defaults to
  `calendar_name`) must match `^[A-Za-z0-9][A-Za-z0-9_-]*$`: no dots —
  dots separate the path segments — and no spaces. An invalid name is
  rejected with 400 before anything is written to the store.
- `calendar_name` must equal the calendar's **display name on the CalDAV
  server** exactly. The route only registers the calendar; create it
  server-side first. A mismatched name still provisions (the route never
  contacts the server) and fails at first use — the tool's not-found
  error then lists the server's available names, which is the recovery
  path (there is no discovery endpoint).

**Security tradeoff.** The per-user secret each password is encrypted
with lives in the same SQLite file as the ciphertext it protects. A leak
of the ciphertext alone — an old backup fragment, a copied row, a dump
synced elsewhere — exposes nothing without the matching secret row; an
attacker who obtains the database file gets both halves and recovers the
passwords. That tradeoff is deliberate: homelab convenience, not
KMS-grade at-rest protection. Treat the store file itself as secret.

**Collisions (409).** The route refuses instead of guessing when:

- the personal config is already provisioned for a *different* user (two
  usernames can sanitize to the same name);
- the chosen `remote_name` exists with a different URL or CalDAV username
  (or belongs to a passthrough remote) — choose another name;
- the existing remote's stored password cannot be decrypted with the
  caller's secret — choose another name;
- the calendar already exists under that config and remote (a genuine
  duplicate).

Retrying an interrupted call is safe: identical connection details reuse
the stored remote (a changed password refreshes the token) until the
calendar row exists — a completed call re-POSTed ends in the
duplicate-calendar 409.

**Degradation and recovery.** Once the grant is revoked or the user
deleted, nothing left can decrypt the remote's password: the loader only
tries secrets of users currently granted the config — a revoked grant's
secret stops counting immediately, and a deleted user's secret row is
gone with them. At the next store load — server start, or any later
successful `POST /user/calendars`, which re-reads the store — the server
logs a warning naming the dropped remote and config, omits it, and keeps
loading everything else: the dotted path then stops resolving — write
tools answer with the generic unknown-path error, read fan-outs find no
accessible scope. Recovery depends on the cause:

- **Grant revoked, user intact** — re-grant the config through the
  [admin REST API](api.md#admin-rest-api); the remote returns at the next
  store load (a restart, or the next successful `POST /user/calendars`).
- **User deleted** — re-create the user, then re-POST the same body with
  a fresh `remote_name`: the old token died with the deleted user's
  secret, so the old name answers 409. Alternatively, delete the orphaned
  `user-<name>` config with the CLI and re-POST under the original name.

**Rate limiting.** This route shares the MCP endpoint's auth limiter, so
a rate-limited caller gets **401** carrying the limiter's `retry in Ns`
hint — deliberately not the admin API's 429: re-checking the limiter here
would double-count the failure.

## Required env vars

All three must be set:

| Variable | Description |
|----------|-------------|
| `DB_CONFIG_ENABLED` | `true` to enable pro mode |
| `CALDAV_MCP_DB_PATH` | Path to the SQLite config store |
| `CALDAV_MCP_CONFIG_SECRET` | Master secret for credential encryption |

## Deploying the SQLite store

The config store is a SQLite file on disk. Mount it into the container using
a named volume or a bind mount:

```yaml
# docker-compose.yaml — named volume example
services:
  caldav-mcp:
    volumes:
      - caldav-config:/data
    environment:
      CALDAV_MCP_DB_PATH: /data/store.db
      CALDAV_MCP_CONFIG_SECRET: ${CALDAV_MCP_CONFIG_SECRET}

volumes:
  caldav-config:
    # Pin the volume name so tooling (e.g. the chown command below) targets
    # this exact volume instead of a project-prefixed <project>_caldav-config.
    name: caldav-config
```

A fresh named volume creates `/data` owned by root, which the container
cannot write until you hand it over once:
`docker run --rm -v caldav-config:/data --user root alpine chown 100:101 /data`
(or `<uid>:<gid>` if you run with `user:`) — see
[Store permissions and UID mapping](#store-permissions-and-uid-mapping).

```bash
# Bind-mount example
docker run -d \
  -v /host/path/store.db:/data/store.db \
  -e CALDAV_MCP_DB_PATH=/data/store.db \
  -e CALDAV_MCP_CONFIG_SECRET=your-secret \
  -e DB_CONFIG_ENABLED=true \
  ghcr.io/gelse/caldav-mcp:latest
```

Binding a single *file* requires the host file to exist beforehand —
Docker creates a missing bind source as a directory, which the server
rejects (`store path is a directory`). Create it first
(`touch /host/path/store.db`), or bind the directory instead
(`-v /host/path:/data` with `CALDAV_MCP_DB_PATH=/data/store.db`), which
also enables first-start auto-create.

On first start the store file does not need to exist yet: when
`CALDAV_MCP_DB_PATH` names a missing file whose parent directory exists
and is writable, the server creates an empty store (schema only — no
users, no configs) and logs a warning saying exactly that. The server
never creates missing parent directories: a missing or unwritable parent
fails startup with an actionable error naming the path, the running
uid/gid, and the fix. The created file is owned by the uid the server
runs as — see [Store permissions and UID mapping](#store-permissions-and-uid-mapping)
if a host-side CLI must access it.

An empty store authenticates no one. Populate configs, remotes and
calendars with the CLI, and users with the admin REST API or the CLI:

```bash
export CALDAV_MCP_CONFIG_SECRET=your-secret
caldav-mcp-config --db store.db config add --name work
caldav-mcp-config --db store.db user add --username alice --key mykey
```

After modifying the store via the CLI (adding/removing configs, users, or
remotes), **restart the server** to pick up changes. User changes made
through the admin API apply on the next request instead — with one
exception: a config added after startup needs a restart before a grant for
it becomes reachable.

## Store permissions and UID mapping

The server writes to the store as the uid it runs as, so the store directory
must be writable by that uid — and by whatever runs `caldav-mcp-config` on
the host. Pick one of these two mappings and use it for both.

### Option A — run the container as your uid (recommended)

```yaml
services:
  caldav-mcp:
    image: ghcr.io/gelse/caldav-mcp:latest
    user: "${PUID:-1000}:${PGID:-1000}"
    ports:
      - "8600:8080"
    environment:
      DB_CONFIG_ENABLED: "true"
      CALDAV_MCP_DB_PATH: /data/store.db
      CALDAV_MCP_CONFIG_SECRET: ${CALDAV_MCP_CONFIG_SECRET}
      CALDAV_MCP_ADMIN_KEY: ${CALDAV_MCP_ADMIN_KEY}
    volumes:
      - ./data:/data
```

```bash
mkdir -p data
export PUID="$(id -u)" PGID="$(id -g)"
docker compose up -d
```

The image runs unchanged as any uid — no path in it depends on a specific
user — so `./data` (store file, SQLite journal files, CLI access) stays owned
by your host user. The first start creates `./data/store.db` as that same
uid, so the CLI works before or after the first start, as your own user.

### Option B — chown the store to the image's user

Without a `user:` override the container runs as the image's `app` user
(`uid=100`, `gid=101`). Make the store match it:

```bash
mkdir -p data
chown -R 100:101 data
```

Tradeoff: your host user no longer owns the store, so running
`caldav-mcp-config` on the host needs access to uid 100 (e.g. `sudo`) — the
store must stay accessible to **both** the container and the CLI.

### Why writes matter

Setting `CALDAV_MCP_ADMIN_KEY` makes the server a store writer: every admin
mutation writes user rows as the server's uid. A store that is readable but
not writable still starts and still serves reads — but admin mutations
return 503 and startup logs a warning naming the uid/gid that lacks write
access. Leave `CALDAV_MCP_ADMIN_KEY` unset for deliberately read-only
stores.

### Troubleshooting

**Symptom** startup fails with `Pro-mode config store not found: … parent
directory … does not exist`: `CALDAV_MCP_DB_PATH` points beneath a
directory that was never created or mounted (the auto-create covers the
*file*, never a directory — an empty volume still provides the parent
directory). **Fix** create the directory (e.g. `mkdir -p ./data`) or
correct the path.

**Symptom** startup fails with `Pro-mode config store permission denied:
cannot create …`: the parent directory exists but the running uid cannot
write to it — a fresh named volume's root-owned `/data` is the typical
case. **Fix** initialize the volume once (named-volume chown command in
[Deploying the SQLite store](#deploying-the-sqlite-store) above) or pick
option A or option B above.

**Symptom** startup fails although the store file exists: older images
misreport EACCES as `Pro-mode config store not found: '/data/store.db'` (or a
raw `PermissionError` traceback); current images print
`Pro-mode config store permission denied: … as uid=… gid=…` and name both
remedies inline. **Cause** a host dir owned by your uid (e.g. `1000:1000`)
mounted at `/data` while the container runs as a different uid. **Fix**
option A or option B above.

## References

- [`docs/cli.md`](cli.md) — full CLI reference for managing the store
- [`docs/api.md`](api.md) — addressing model and aggregated result format
- [`docs/api.md`](api.md#admin-rest-api) — Admin REST API: routes, status
  codes, examples, and restart/reload semantics for managing users
  (create/delete, key rotation, grants) over HTTP
- [`docs/api.md`](api.md#user-calendar-provisioning) — User calendar
  provisioning: the `POST /user/calendars` route, its schema, status
  codes, and reload semantics
- [`docs/ui.md`](ui.md) — admin UI: manages users through the Admin REST
  API and is served at `/ui` when `SHOW_UI=true` (independent of pro mode;
  without the admin API it shows its “admin API not available” screen)
- [`docs/architecture.md`](architecture.md) — fan-out execution model and config singleton design
