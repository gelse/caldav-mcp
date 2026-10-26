"""Pro-mode integration test fixtures.

Builds a temporary SQLite store via ConfigStore + encrypt_secret + hash_api_key
and sets the env vars the MCP server needs to boot in DB-config mode.

The topology is the **union** of the M4.5 and M5.2 requirements, because both
suites share the single ``mcp-pro`` compose service (port 8080) and therefore
must be served by one store:

* **main** → remote ``radicale`` (direct: userA) → calendars ``personal``, ``work``
* **main** → remote ``rad1`` (direct: testuser) → calendar ``personal``
* **mirror** → remote ``radicale-mirror`` (direct: userB) → calendar ``shared``
* **second** → remote ``rad2`` (direct: testuser on server 2) → calendar ``work``
* **broken** → remote ``dead`` (direct: unreachable ``localhost:59999``)
* **passthrough** → remote ``relay`` (passthrough: radicale1, headers-supplied creds)

Users:

* **alice** → granted ``["main", "mirror", "second", "broken", "passthrough"]``
* **bob** → granted ``["main", "second"]`` only

``main`` intentionally carries two remotes so that M4.5's dotted-path write
(``main.radicale.work``) and M5.2's direct-remote case (``main.rad1.personal``)
both resolve against the same config name.
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

# M4.5 remote identities (both on Radicale server 1).
USER_A = "userA"
USER_A_PASS = "testpassA"
USER_B = "userB"
USER_B_PASS = "testpassB"

# M5.2 remote identity (present on both Radicale servers).
USER_SHARED = "testuser"
USER_SHARED_PASS = "testpass"

ALICE_KEY = "alice-integration-test-key"
BOB_KEY = "bob-integration-test-key"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_pro_store(
    db_path: str,
    radicale_url: str,
    radicale2_url: str = "http://localhost:5233",
) -> None:
    """Populate a ConfigStore with five configs, two users, and their grants.

    * **main** → remotes ``radicale`` (M4.5, userA) and ``rad1`` (M5.2, testuser)
    * **mirror** → remote ``radicale-mirror`` (M4.5, userB)
    * **second** → remote ``rad2`` (M5.2, testuser on Radicale server 2)
    * **broken** → remote ``dead`` (unreachable endpoint)
    * **passthrough** → remote ``relay`` (passthrough, headers-supplied creds)

    Users:

    * **alice** → granted all five configs
    * **bob** → granted ``["main", "second"]`` only
    """
    enc_a = encrypt_secret(USER_A_PASS)
    enc_b = encrypt_secret(USER_B_PASS)
    enc_shared = encrypt_secret(USER_SHARED_PASS)
    enc_dead = encrypt_secret("dead-pass")
    alice_hash = hash_api_key(ALICE_KEY)
    bob_hash = hash_api_key(BOB_KEY)

    with ConfigStore(db_path) as store:
        # Config: main → radicale (M4.5, userA, personal+work)
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

        # Config: main → rad1 (M5.2, testuser on server 1)
        store.create_remote(
            config_name="main",
            remote=RemoteRecord(
                config_name="main",
                name="rad1",
                url=radicale_url,
                auth_mode="direct",
                username=USER_SHARED,
                password_enc=enc_shared,
            ),
        )
        store.create_calendar(
            config_name="main",
            remote_name="rad1",
            name="personal",
        )

        # Config: mirror → radicale-mirror (M4.5, userB)
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

        # Config: second → rad2 (M5.2, testuser on server 2)
        store.create_config("second")
        store.create_remote(
            config_name="second",
            remote=RemoteRecord(
                config_name="second",
                name="rad2",
                url=radicale2_url,
                auth_mode="direct",
                username=USER_SHARED,
                password_enc=enc_shared,
            ),
        )
        store.create_calendar(
            config_name="second",
            remote_name="rad2",
            name="work",
        )

        # Config: broken → dead (unreachable endpoint)
        store.create_config("broken")
        store.create_remote(
            config_name="broken",
            remote=RemoteRecord(
                config_name="broken",
                name="dead",
                url="http://localhost:59999",
                auth_mode="direct",
                username="nobody",
                password_enc=enc_dead,
            ),
        )

        # Config: passthrough → relay (headers-supplied creds against server 1).
        # ``work`` belongs to the testuser2 identity the passthrough tests
        # authenticate as, so a dotted path (``passthrough.relay.work``) has a
        # real collection to resolve to.
        store.create_config("passthrough")
        store.create_remote(
            config_name="passthrough",
            remote=RemoteRecord(
                config_name="passthrough",
                name="relay",
                url=radicale_url,
                auth_mode="passthrough",
                username="",
                password_enc="",
            ),
        )
        store.create_calendar(
            config_name="passthrough",
            remote_name="relay",
            name="work",
        )

        # Users
        store.create_user(username="alice", key_hash=alice_hash)
        store.grant_config(username="alice", config_name="main")
        store.grant_config(username="alice", config_name="mirror")
        store.grant_config(username="alice", config_name="second")
        store.grant_config(username="alice", config_name="broken")
        store.grant_config(username="alice", config_name="passthrough")

        store.create_user(username="bob", key_hash=bob_hash)
        store.grant_config(username="bob", config_name="main")
        store.grant_config(username="bob", config_name="second")


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pro_store_path(
    tmp_path_factory: pytest.TempPathFactory,
    radicale_url: str,
    radicale2_url: str,
) -> str:
    """Build the pro-mode SQLite store once per test session.

    Returns the filesystem path to the store file.  The store is created
    with the union of the M4.5 (``main``, ``mirror``) and M5.2 (``main``/``rad1``,
    ``second``, ``broken``, ``passthrough``) topologies.
    """
    tmp_dir = tmp_path_factory.mktemp("pro-store")
    db_path = str(tmp_dir / "test.db")
    _build_pro_store(db_path, radicale_url, radicale2_url)
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
