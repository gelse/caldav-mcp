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
- `test_mcp_auth.py` — MCP endpoint auth headers, Bearer vs X-Api-Key (issue #18);
  boots `server.py` in a subprocess, does not need Radicale

## Configuration

The Radicale test server uses:
- **User:** `testuser` / **Password:** `testpass`
- **Port:** `5232`
- **Storage:** Filesystem-backed (`tests/integration/radicale-data/`)

Environment variables to override defaults:
- `RADICALE_URL` — Server URL (default: `http://localhost:5232`)
- `RADICALE_USER` — Username (default: `testuser`)
- `RADICALE_PASS` — Password (default: `testpass`)
