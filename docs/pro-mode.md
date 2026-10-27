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
- **Users apply live, configs still need a restart**: The store is read
  once at startup and frozen into an immutable `AppConfig`, so config,
  remote, and calendar changes require a server restart. User changes made
  through the [admin REST API](api.md#admin-rest-api) (create, delete, key
  rotation, grant, revoke,
  replace grants) take effect on the next request instead — their
  responses no longer carry `restart_required` (removed with M5 users-only
  hot reload); a failed live reload is reported as `"reload": "failed"`.
  One caveat: granting a config that was added after startup responds with
  `config_topology_restart_required: true` — the grant applies, but
  reaching that config still needs a restart.

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
```

```bash
# Bind-mount example
docker run -d \
  -v /host/path/store.db:/data/store.db \
  -e CALDAV_MCP_DB_PATH=/data/store.db \
  -e CALDAV_MCP_CONFIG_SECRET=your-secret \
  -e DB_CONFIG_ENABLED=true \
  ghcr.io/gelse/caldav-mcp:latest
```

Initialize the store before first start:

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
by your host user. Initialize `./data/store.db` with the CLI before the first
start, as your own user.

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
- [`docs/ui.md`](ui.md) — admin UI: manages users through the Admin REST
  API and is served at `/ui` when `SHOW_UI=true` (independent of pro mode;
  without the admin API it shows its “admin API not available” screen)
- [`docs/architecture.md`](architecture.md) — fan-out execution model and config singleton design
