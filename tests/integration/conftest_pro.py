"""Pro-mode integration test fixtures.

Builds a temporary SQLite store via ConfigStore + encrypt_secret + hash_api_key
and sets the env vars the MCP server needs to boot in DB-config mode.

Two configs share the same Radicale test server with different user accounts:

* **main** → remote ``radicale`` (user A) → calendars ``personal``, ``work``
* **mirror** → remote ``radicale-mirror`` (user B) → calendar ``shared``

Users:

* **alice** → granted ``["main", "mirror"]``
* **bob** → granted ``["main"]`` only
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest

from caldav_mcp.config_crypto import encrypt_secret
from caldav_mcp.config_store import ConfigStore, RemoteRecord
from caldav_mcp.key_hash import hash_api_key

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRO_STORE_SECRET = "test-integration-pro-secret-do-not-use-in-production"
USER_A = "userA"
USER_A_PASS = "testpassA"
USER_B = "userB"
USER_B_PASS = "testpassB"

ALICE_KEY = "alice-integration-test-key"
BOB_KEY = "bob-integration-test-key"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_pro_store(db_path: str, radicale_url: str) -> None:
    """Populate a ConfigStore with two configs, two users, and their grants.

    Both remotes point at the same Radicale instance (different user accounts).
    """
    enc_a = encrypt_secret(USER_A_PASS)
    enc_b = encrypt_secret(USER_B_PASS)
    alice_hash = hash_api_key(ALICE_KEY)
    bob_hash = hash_api_key(BOB_KEY)

    with ConfigStore(db_path) as store:
        # Config: main
        store.create_config("main")
        store.create_remote(
            config_name="main",
            remote=RemoteRecord(
                config_name="main",
                name="radicale",
                url=radicale_url,
                auth_mode="direct",
                username=USER_A,
                password_enc=enc_a,
            ),
        )
        store.create_calendar(
            config_name="main",
            remote_name="radicale",
            name="personal",
        )
        store.create_calendar(
            config_name="main",
            remote_name="radicale",
            name="work",
        )

        # Config: mirror
        store.create_config("mirror")
        store.create_remote(
            config_name="mirror",
            remote=RemoteRecord(
                config_name="mirror",
                name="radicale-mirror",
                url=radicale_url,
                auth_mode="direct",
                username=USER_B,
                password_enc=enc_b,
            ),
        )
        store.create_calendar(
            config_name="mirror",
            remote_name="radicale-mirror",
            name="shared",
        )

        # Users
        store.create_user(username="alice", key_hash=alice_hash)
        store.grant_config(username="alice", config_name="main")
        store.grant_config(username="alice", config_name="mirror")

        store.create_user(username="bob", key_hash=bob_hash)
        store.grant_config(username="bob", config_name="main")


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pro_store_path(tmp_path_factory: pytest.TempPathFactory, radicale_url: str) -> str:
    """Build the pro-mode SQLite store once per test session.

    Returns the filesystem path to the store file.  The store is created
    with two configs (``main`` and ``mirror``) pointing at the same
    Radicale test server with different user credentials.
    """
    tmp_dir = tmp_path_factory.mktemp("pro-store")
    db_path = str(tmp_dir / "test.db")
    _build_pro_store(db_path, radicale_url)
    return db_path


@pytest.fixture(scope="session", autouse=True)
def _pro_mode_env(pro_store_path: str) -> Generator[None, None, None]:
    """Set env vars required for pro-mode server boot.

    Sets ``DB_CONFIG_ENABLED``, ``CALDAV_MCP_DB_PATH``, and
    ``CALDAV_MCP_CONFIG_SECRET`` for the duration of the test session.
    """
    old_vals: dict[str, str | None] = {}
    env_map = {
        "DB_CONFIG_ENABLED": "true",
        "CALDAV_MCP_DB_PATH": pro_store_path,
        "CALDAV_MCP_CONFIG_SECRET": PRO_STORE_SECRET,
    }
    for key, val in env_map.items():
        old_vals[key] = os.environ.get(key)
        os.environ[key] = val

    yield

    for key, old_val in old_vals.items():
        if old_val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old_val
