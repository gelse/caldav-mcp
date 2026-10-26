"""Build the pro-mode SQLite store consumed by the ``mcp-pro`` compose service.

Run before ``docker compose -f docker-compose.test.yaml up`` (the Makefile's
``test-integration`` target invokes this script automatically)::

    CALDAV_MCP_CONFIG_SECRET=<secret> python3 tests/integration/build_pro_store.py [db_path]

The topology is defined once, in :mod:`tests.integration.conftest_pro`, so the
store consumed by the ``mcp-pro`` container matches the fixtures used by
``test_pro_mode.py``.  The remotes point at ``http://radicale:5232`` (the
compose service DNS name), not ``localhost``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Project root (two levels up from this file) must be importable so the
# caldav_mcp package and the integration conftest resolve.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

# The build must use the same master secret the server is started with; the
# compose service hard-codes this value in docker-compose.test.yaml.
DEFAULT_DB_PATH = str(_PROJECT_ROOT / "tests" / "integration" / "pro-store" / "pro-store.db")
RADICALE_URL = "http://radicale:5232"
RADICALE2_URL = "http://radicale2:5232"


def main() -> int:
    db_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH

    os.environ.setdefault(
        "CALDAV_MCP_CONFIG_SECRET", "test-integration-pro-secret-do-not-use-in-production"
    )

    from tests.integration.conftest_pro import _build_pro_store

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # Rebuild from scratch so repeated runs are idempotent.
    for stale in (Path(db_path), Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        stale.unlink(missing_ok=True)
    _build_pro_store(db_path, RADICALE_URL, RADICALE2_URL)

    # Deterministic permissions for the container's Alpine *system* user
    # ``app`` (Dockerfile ``adduser -S``): relative to host-owned artifacts it
    # is "other", and without a writable directory (SQLite journal files) and
    # a writable DB file, admin-API writes fail with sqlite3.OperationalError
    # → 503 even though the compose mount is read-write (M3.1 round-2
    # CRITICAL review finding — applied regardless of the host uid/umask).
    Path(db_path).parent.chmod(0o777)
    Path(db_path).chmod(0o666)

    print(f"Pro store built: {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
