# Integration Tests

## Prerequisites

- Docker and Docker Compose installed
- Ports 5232, 5233, 8080, 8081, 8082 available

## Running

1. Run the integration tests (Radicale is started and stopped automatically):
   ```bash
   make test-integration
   ```

   This will:
   - Start the Radicale test server in Docker
   - Wait for it to be healthy
   - Run the integration test suite
   - Tear down the server and clean up volumes

2. Or manually:
   ```bash
   docker compose -f docker-compose.test.yaml up -d
   .venv/bin/python -m pytest tests/integration/ -m integration
   docker compose -f docker-compose.test.yaml down -v
   ```

## Test Structure

- `test_crdl_lifecycle.py` — Full CRUD lifecycle for calendars and events
- `test_multi_calendar.py` — Operations across multiple calendars
- `test_concurrent_access.py` — Concurrent read/write patterns
- `test_pro_mode.py` — Pro-mode DB auth, fan-out reads, dotted-path writes
- `test_multi_remote_fanout.py` — Multi-remote fan-out, partial failure, passthrough, header/env edge cases

## Configuration

### Radicale servers

Two independent Radicale servers run for multi-remote tests:

| Service | Container | Port | Config dir | Users |
|---------|-----------|------|------------|-------|
| `radicale` | `radicale-test` | `5232` | `tests/integration/radicale-config/` | `testuser`, `testuser2`, `userC`, `userA`, `userB` |
| `radicale2` | `radicale2-test` | `5233` | `tests/integration/radicale2-config/` | `testuser` |

Both use filesystem-backed storage (`tests/integration/radicale-data/` and `tests/integration/radicale2-data/` respectively).

Identity roles on server 1: `testuser` backs the direct fan-out remotes,
`testuser2` is the passthrough remote's identity, `userA`/`userB` back the
M4.5 pro-mode remotes, and `userC` exists so the bad-credentials remote can use
a username no healthy remote uses (the `DAVClient` cache keys on
`(url, username)` and direct-mode hits skip password verification, so a shared
username would serve a healthy remote's cached session).

> **Radicale auth caveat.** This Radicale deployment does not reject bad
> credentials for read `PROPFIND` — a wrong password, no credentials, and even
> an unknown user all return HTTP 207. A wrong-password remote therefore reads
> successfully rather than surfacing as an auth failure. See the
> `TestBadCredentials` docstring in `test_multi_remote_fanout.py`.

### MCP server services

| Service | Container | Port | Mode | Purpose |
|---------|-----------|------|------|---------|
| `mcp-pro` | `caldav-mcp-pro-test` | `8080` | Pro (DB) | Pro-mode fan-out tests |
| `mcp-simple` | `caldav-mcp-simple-test` | `8081` | Simple header | Header-mode edge cases |
| `mcp-env` | `caldav-mcp-env-test` | `8082` | Simple env | Env-mode headers-ignored tests |

### Environment variables

- `RADICALE_URL` — Server 1 URL (default: `http://localhost:5232`)
- `RADICALE_USER` — Server 1 username (default: `testuser`)
- `RADICALE_PASS` — Server 1 password (default: `testpass`)
- `RADICALE2_URL` — Server 2 URL (default: `http://localhost:5233`)
- `RADICALE2_USER` — Server 2 username (default: `testuser`)
- `RADICALE2_PASS` — Server 2 password (default: `testpass`)

### Pro-mode tests

Pro-mode tests (`test_pro_mode.py`, `test_multi_remote_fanout.py`) require the
`mcp-pro` Docker Compose service running with `DB_CONFIG_ENABLED=true`. The
Makefile builds the SQLite store automatically before starting compose. The MCP
server is available at `http://localhost:8080/mcp`.

Because both modules share that one service, the store built by
`tests/integration/build_pro_store.py` (topology defined in `conftest_pro.py`)
carries the **union** of their requirements: `main` → remotes `radicale`
(userA) and `rad1` (testuser); `mirror` → `radicale-mirror` (userB);
`second` → `rad2` (testuser on server 2); `broken` → `dead`
(`http://localhost:59999`, nothing listening); `passthrough` → `relay`
(headers-supplied credentials). `alice` is granted every config, `bob` only
`main` and `second`.

One consequence for request headers: header-mode and passthrough cases pass the
CalDAV URL *through* an MCP server that runs in a container, so the URL must be
container-reachable (`http://radicale:5232`) — `http://localhost:5232` resolves
to the MCP container itself and is refused.
