# Integration Tests

## Prerequisites

- Docker and Docker Compose installed
- Port 5232 available

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

## Configuration

The Radicale test server uses:
- **Users:** `testuser` / `testpass` (simple mode), `userA` / `testpassA`, `userB` / `testpassB` (pro mode)
- **Port:** `5232`
- **Storage:** Filesystem-backed (`tests/integration/radicale-data/`)

Environment variables to override defaults:
- `RADICALE_URL` — Server URL (default: `http://localhost:5232`)
- `RADICALE_USER` — Username (default: `testuser`)
- `RADICALE_PASS` — Password (default: `testpass`)

### Pro-mode tests

Pro-mode tests (`test_pro_mode.py`) require the `mcp-pro` Docker Compose service
running with `DB_CONFIG_ENABLED=true`. The Makefile builds the SQLite store
automatically before starting compose. The MCP server is available at
`http://localhost:8080/mcp`.
