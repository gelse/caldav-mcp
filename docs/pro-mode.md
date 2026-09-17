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
- **Restart-to-apply**: Config changes require a server restart. The store
  is read once at startup and frozen into an immutable `AppConfig`.

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

After modifying the store (adding/removing configs, users, or remotes),
**restart the server** to pick up changes.

## References

- [`docs/cli.md`](cli.md) — full CLI reference for managing the store
- [`docs/api.md`](api.md) — addressing model and aggregated result format
- [`docs/architecture.md`](architecture.md) — fan-out execution model and config singleton design
