# Integration Tests

## Prerequisites

- Docker and Docker Compose installed
- Port 5232 available

## Running

1. Start the Radicale test server:
   ```bash
   docker compose -f docker-compose.test.yaml up -d
   ```

2. Wait for Radicale to be healthy:
   ```bash
   docker compose -f docker-compose.test.yaml ps
   ```

3. Run integration tests:
   ```bash
   make test-integration
   # or: .venv/bin/python -m pytest tests/integration/ -m integration
   ```

4. Stop the test server:
   ```bash
   docker compose -f docker-compose.test.yaml down -v
   ```

## Test Structure

- `test_crdl_lifecycle.py` — Full CRUD lifecycle for calendars and events
- `test_multi_calendar.py` — Operations across multiple calendars
- `test_concurrent_access.py` — Concurrent read/write patterns

## Configuration

The Radicale test server uses:
- **User:** `testuser` / **Password:** `testpass`
- **Port:** `5232`
- **Storage:** Filesystem-backed (`tests/integration/radicale-data/`)

Environment variables to override defaults:
- `RADICALE_URL` — Server URL (default: `http://localhost:5232`)
- `RADICALE_USER` — Username (default: `testuser`)
- `RADICALE_PASS` — Password (default: `testpass`)
