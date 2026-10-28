"""Unit tests for caldav_mcp.config_store (SQLite configuration store).

Uses pytest's ``tmp_path`` fixture with a real SQLite database on disk.
All tests follow the plain-function style of tests/unit/test_auth.py.
"""

from __future__ import annotations

import dataclasses
import os
import sqlite3
from pathlib import Path

import pytest

from caldav_mcp.config_store import (
    SCHEMA_VERSION,
    CalendarRecord,
    ConfigSnapshot,
    ConfigStore,
    RemoteRecord,
    StoreConflictError,
    StoreError,
    StoreNotFoundError,
    StoreOpenError,
    StoreSchemaError,
    StoreValidationError,
    UserRecord,
)
from caldav_mcp.key_hash import hash_api_key, verify_api_key

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _store_path(tmp_path, name="test.db"):
    return str(tmp_path / name)


def _make_remote(config_name="cfg", name="rem", **overrides):
    defaults = dict(
        config_name=config_name,
        name=name,
        url="https://cal.example.com",
        auth_mode="direct",
        username="user",
        password_enc="secret",
    )
    defaults.update(overrides)
    return RemoteRecord(**defaults)


# ---------------------------------------------------------------------------
# 1. Schema creation / idempotency
# ---------------------------------------------------------------------------


def test_schema_creation_creates_version_row(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        conn = store._get_conn()
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row is not None
        assert row[0] == SCHEMA_VERSION


def test_schema_creation_creates_all_tables(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        conn = store._get_conn()
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        expected = {"schema_version", "users", "user_configs", "configs", "remotes", "calendars"}
        assert expected.issubset(tables)


def test_schema_idempotent(tmp_path):
    path = _store_path(tmp_path)
    with ConfigStore(path):
        pass
    # Reopening should not raise.
    with ConfigStore(path) as store:
        row = store._get_conn().execute("SELECT version FROM schema_version").fetchone()
        assert row[0] == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# 2. Forward-compatibility guard
# ---------------------------------------------------------------------------


def test_newer_schema_version_raises(tmp_path):
    path = _store_path(tmp_path)
    with ConfigStore(path):
        pass
    # Tamper: bump version to SCHEMA_VERSION + 1.
    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
    conn.commit()
    conn.close()
    with pytest.raises(StoreSchemaError, match="newer than supported"):
        ConfigStore(path).__enter__()


# ---------------------------------------------------------------------------
# 2b. Open-path failures → typed StoreError, never an opaque sqlite3 error
# ---------------------------------------------------------------------------

_IS_ROOT = getattr(os, "geteuid", lambda: -1)() == 0


@pytest.mark.skipif(_IS_ROOT, reason="root bypasses filesystem permission checks")
def test_open_unreadable_file_raises_typed_store_error(tmp_path):
    """EACCES on the store file itself → typed StoreError naming path + uid/gid."""
    path = _store_path(tmp_path, "unreadable.db")
    p = Path(path)
    p.write_text("placeholder")
    p.chmod(0o000)
    try:
        with pytest.raises(StoreError) as excinfo:
            ConfigStore(path)
    finally:
        p.chmod(0o600)

    msg = str(excinfo.value)
    assert "permission denied" in msg.lower(), f"expected permission-denied text: {msg!r}"
    assert path in msg, f"expected store path in message: {msg!r}"
    assert f"uid={os.getuid()}" in msg, f"expected running uid in message: {msg!r}"
    assert f"gid={os.getgid()}" in msg, f"expected running gid in message: {msg!r}"
    # The typed error must be StoreOpenError (a StoreError subclass), not
    # sqlite3.OperationalError leaking through.
    assert isinstance(excinfo.value, StoreOpenError)


def test_open_directory_path_raises_typed_store_error(tmp_path):
    """A directory at the store path → typed StoreError with a directory message."""
    path = str(tmp_path)
    with pytest.raises(StoreError) as excinfo:
        ConfigStore(path)

    msg = str(excinfo.value)
    assert "directory" in msg.lower(), f"expected directory text: {msg!r}"
    assert path in msg, f"expected store path in message: {msg!r}"
    assert isinstance(excinfo.value, StoreOpenError)


def test_open_operational_error_is_wrapped_typed(monkeypatch, tmp_path):
    """Root-safe variant: sqlite3.connect failure → typed StoreError, sqlite text kept."""
    path = _store_path(tmp_path, "wrapped.db")

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr("caldav_mcp.config_store.sqlite3.connect", boom)

    with pytest.raises(StoreError) as excinfo:
        ConfigStore(path)

    msg = str(excinfo.value)
    assert path in msg
    assert "unable to open database file" in msg
    assert isinstance(excinfo.value, StoreOpenError)
    assert not isinstance(excinfo.value, sqlite3.OperationalError)


# ---------------------------------------------------------------------------
# 3. User CRUD + cascade
# ---------------------------------------------------------------------------


def test_user_create_and_get(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_user("alice", "hash123")
        user = store.get_user("alice")
        assert user == UserRecord(username="alice", key_hash="hash123", config_names=())


def test_user_list(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_user("alice", "h1")
        store.create_user("bob", "h2")
        users = store.list_users()
        assert len(users) == 2
        assert users[0].username == "alice"
        assert users[1].username == "bob"


def test_user_set_configs(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("c1")
        store.create_config("c2")
        store.create_user("alice", "h")
        store.set_user_configs("alice", ["c1", "c2"])
        user = store.get_user("alice")
        assert user.config_names == ("c1", "c2")


def test_user_delete_cascades_user_configs(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("c1")
        store.create_user("alice", "h")
        store.set_user_configs("alice", ["c1"])
        store.delete_user("alice")
        assert store.list_users() == ()
        # user_configs row should be gone too (cascade).
        conn = store._get_conn()
        assert conn.execute("SELECT COUNT(*) FROM user_configs").fetchone()[0] == 0


def test_user_delete_not_found(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        with pytest.raises(StoreNotFoundError, match="user not found"):
            store.delete_user("nobody")


# ---------------------------------------------------------------------------
# 3b. set_user_key_hash (key rotation)
# ---------------------------------------------------------------------------


def test_set_user_key_hash_rotation(tmp_path):
    """Rotation persists: old key stops verifying, new key verifies."""
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_user("alice", hash_api_key("old-key"))
        store.set_user_key_hash("alice", hash_api_key("new-key"))
        user = store.get_user("alice")
        assert verify_api_key("old-key", user.key_hash) is False
        assert verify_api_key("new-key", user.key_hash) is True


def test_set_user_key_hash_preserves_grants(tmp_path):
    """Rotation touches only key_hash — existing grants stay linked."""
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("c1")
        store.create_user("alice", hash_api_key("old-key"))
        store.set_user_configs("alice", ["c1"])
        store.set_user_key_hash("alice", hash_api_key("new-key"))
        user = store.get_user("alice")
        assert user.config_names == ("c1",)
        assert verify_api_key("new-key", user.key_hash) is True


def test_set_user_key_hash_unknown_user(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        with pytest.raises(StoreNotFoundError, match="user not found"):
            store.set_user_key_hash("ghost", "h")


def test_set_user_key_hash_empty_key_hash_rejected(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_user("alice", "h")
        with pytest.raises(StoreValidationError, match="key_hash must not be empty"):
            store.set_user_key_hash("alice", "")


def test_set_user_key_hash_empty_username_rejected(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        with pytest.raises(StoreValidationError, match="username must not be empty"):
            store.set_user_key_hash("", "h")


# ---------------------------------------------------------------------------
# 3c. Per-user secret (enc_secret) — schema v2, migration/backfill, reads
# ---------------------------------------------------------------------------


# Verbatim schema-1 DDL (pre-enc_secret): used to build migration fixtures.
_V1_DDL = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    username     TEXT PRIMARY KEY,
    key_hash     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_configs (
    username     TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    config_name  TEXT NOT NULL REFERENCES configs(name)    ON DELETE CASCADE,
    PRIMARY KEY (username, config_name)
);

CREATE TABLE IF NOT EXISTS configs (
    name         TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS remotes (
    config_name  TEXT NOT NULL REFERENCES configs(name) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    url          TEXT NOT NULL,
    auth_mode    TEXT NOT NULL CHECK (auth_mode IN ('direct', 'passthrough')),
    username     TEXT NOT NULL DEFAULT '',
    password_enc TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (config_name, name)
);

CREATE TABLE IF NOT EXISTS calendars (
    config_name  TEXT NOT NULL,
    remote_name  TEXT NOT NULL,
    name         TEXT NOT NULL,
    PRIMARY KEY (config_name, remote_name, name),
    FOREIGN KEY (config_name, remote_name)
        REFERENCES remotes(config_name, name) ON DELETE CASCADE
);
"""


def _user_columns(path):
    conn = sqlite3.connect(path)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    finally:
        conn.close()


def test_fresh_store_is_v2_with_enc_secret_column(tmp_path):
    """Fresh DB → schema version 2 and the users table carries enc_secret."""
    path = _store_path(tmp_path)
    with ConfigStore(path):
        pass
    conn = sqlite3.connect(path)
    try:
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
    finally:
        conn.close()
    assert version == 2
    assert "enc_secret" in _user_columns(path)


def test_create_user_generates_distinct_nonempty_secrets(tmp_path):
    """Two users → auto-generated secrets that are both non-empty and distinct."""
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_user("alice", "h1")
        store.create_user("bob", "h2")
        secrets_map = store.get_user_secrets()
        assert set(secrets_map) == {"alice", "bob"}
        alice_secret, bob_secret = secrets_map["alice"], secrets_map["bob"]
        assert alice_secret, "generated secret must be non-empty"
        assert bob_secret, "generated secret must be non-empty"
        assert alice_secret != bob_secret, "each user must get its own secret"
        # get_user_secret agrees with the bulk read.
        assert store.get_user_secret("alice") == alice_secret


def test_create_user_explicit_enc_secret_honored(tmp_path):
    """The enc_secret parameter is stored verbatim (deterministic tests)."""
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_user("alice", "h", enc_secret="fixed-test-secret")
        assert store.get_user_secret("alice") == "fixed-test-secret"
        assert store.get_user_secrets() == {"alice": "fixed-test-secret"}


def test_v1_store_migrates_to_v2_with_backfill_and_rows_intact(tmp_path):
    """A hand-built schema-1 store (version row 1, no enc_secret column)
    opens on current code: column added, secrets backfilled distinctly,
    version bumped to 2, all pre-existing rows intact."""
    path = _store_path(tmp_path, "v1.db")
    conn = sqlite3.connect(path)
    conn.executescript(_V1_DDL)
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.execute("INSERT INTO users (username, key_hash) VALUES ('alice', 'hash-alice')")
    conn.execute("INSERT INTO users (username, key_hash) VALUES ('bob', 'hash-bob')")
    conn.execute("INSERT INTO configs (name) VALUES ('work')")
    conn.execute("INSERT INTO user_configs (username, config_name) VALUES ('alice', 'work')")
    conn.execute(
        "INSERT INTO remotes (config_name, name, url, auth_mode, username, password_enc) "
        "VALUES ('work', 'nc', 'https://cal.example/dav', 'direct', 'u', 'enc')"
    )
    conn.execute(
        "INSERT INTO calendars (config_name, remote_name, name) VALUES ('work', 'nc', 'personal')"
    )
    conn.commit()
    conn.close()

    assert "enc_secret" not in _user_columns(path), "fixture must start as schema 1"

    with ConfigStore(path) as store:
        # Column added, version bumped.
        assert "enc_secret" in _user_columns(path)
        version = store._get_conn().execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == 2
        # Existing users' secrets backfilled: non-NULL, non-empty, distinct.
        secrets_map = store.get_user_secrets()
        assert set(secrets_map) == {"alice", "bob"}
        assert secrets_map["alice"] and secrets_map["bob"]
        assert secrets_map["alice"] != secrets_map["bob"]
        # Pre-existing rows intact.
        assert store.get_user("alice") == UserRecord(
            username="alice", key_hash="hash-alice", config_names=("work",)
        )
        assert store.get_user("bob") == UserRecord(
            username="bob", key_hash="hash-bob", config_names=()
        )
        snap = store.load_config("work")
        assert snap.remotes == (
            RemoteRecord(
                config_name="work",
                name="nc",
                url="https://cal.example/dav",
                auth_mode="direct",
                username="u",
                password_enc="enc",
            ),
        )
        assert snap.calendars == (CalendarRecord("work", "nc", "personal"),)


def test_crash_recovery_column_present_version_1_null_secret_self_heals(tmp_path):
    """Partial migration state (column added, version still 1, NULL secret)
    self-heals on next open: backfill runs and version → 2."""
    path = _store_path(tmp_path)
    with ConfigStore(path) as store:
        store.create_user("alice", "h")
    # Simulate the crash window: ALTER committed, backfill/version not yet.
    conn = sqlite3.connect(path)
    conn.execute("UPDATE schema_version SET version = 1")
    conn.execute("UPDATE users SET enc_secret = NULL WHERE username = 'alice'")
    conn.commit()
    conn.close()

    with ConfigStore(path) as store:
        version = store._get_conn().execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == 2
        secret = store.get_user_secret("alice")
        assert secret, "NULL secret must be backfilled on reopen"


def test_reopen_fully_migrated_store_issues_no_writes(tmp_path, monkeypatch):
    """Gated backfill: reopening a fully-migrated store issues no write at
    all (INSERT/UPDATE/DELETE/ALTER) — read-only-degraded stores keep working."""
    path = _store_path(tmp_path)
    with ConfigStore(path) as store:
        store.create_user("alice", "h")
        store.create_config("work")
        # Sanity: row writes ARE counted on this connection while seeding.
        assert store._get_conn().total_changes > 0

    write_statements: list[str] = []
    orig_connect = sqlite3.connect

    def _tracing_connect(*args, **kwargs):
        conn = orig_connect(*args, **kwargs)
        conn.set_trace_callback(
            lambda sql: (
                write_statements.append(sql)
                if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "ALTER"))
                else None
            )
        )
        return conn

    monkeypatch.setattr("caldav_mcp.config_store.sqlite3.connect", _tracing_connect)
    with ConfigStore(path) as store:
        assert store._get_conn().total_changes == 0, "open must not change any row"
    assert write_statements == [], f"open must not issue writes: {write_statements}"


def test_list_config_grants_membership_and_order(tmp_path):
    """Granted/not-granted membership plus stable grant (rowid) order."""
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("c1")
        store.create_config("c2")
        store.create_user("alice", "h")
        store.create_user("bob", "h")
        store.create_user("carol", "h")
        # Unknown / never-granted config → empty tuple, no raise.
        assert store.list_config_grants("never-granted") == ()
        assert store.list_config_grants("c1") == ()
        # Grant order is the order grants were written; non-members excluded.
        store.grant_config("bob", "c1")
        store.grant_config("alice", "c1")
        store.grant_config("alice", "c2")
        assert store.list_config_grants("c1") == ("bob", "alice")
        assert store.list_config_grants("c2") == ("alice",)
        assert "carol" not in store.list_config_grants("c1")


def test_get_user_secret_unknown_user_raises(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        with pytest.raises(StoreNotFoundError, match="user not found"):
            store.get_user_secret("ghost")


def test_get_user_secrets_excludes_null_secrets(tmp_path):
    """NULL secrets are omitted from the bulk read (and read as None)."""
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_user("alice", "h")
        store.create_user("bob", "h")
        # Force a NULL via a second connection — no reopen (reopen heals).
        seed = sqlite3.connect(_store_path(tmp_path))
        seed.execute("UPDATE users SET enc_secret = NULL WHERE username = 'alice'")
        seed.commit()
        seed.close()
        assert store.get_user_secret("alice") is None
        secrets_map = store.get_user_secrets()
        assert "alice" not in secrets_map
        assert set(secrets_map) == {"bob"}


def test_user_records_never_expose_enc_secret(tmp_path):
    """UserRecord (ProUser/admin payload source) must not carry the secret."""
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("c1")
        store.create_user("alice", "h")
        store.grant_config("alice", "c1")
        secret = store.get_user_secret("alice")
        assert secret

        user = store.get_user("alice")
        assert "enc_secret" not in {f.name for f in dataclasses.fields(user)}
        assert "enc_secret" not in dataclasses.asdict(user)
        assert secret not in repr(user)

        users = store.list_users()
        for u in users:
            assert "enc_secret" not in {f.name for f in dataclasses.fields(u)}
            assert "enc_secret" not in dataclasses.asdict(u)
            assert secret not in repr(u)


# ---------------------------------------------------------------------------
# 4. Config / remote / calendar CRUD + cascades + load_config round-trip
# ---------------------------------------------------------------------------


def test_config_create_and_list(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("work")
        store.create_config("personal")
        assert store.list_configs() == ("work", "personal")


def test_config_delete_not_found(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        with pytest.raises(StoreNotFoundError, match="config not found"):
            store.delete_config("nope")


def test_config_delete_cascades_remotes_and_calendars(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        store.create_remote("cfg", _make_remote("cfg", "rem"))
        store.create_calendar("cfg", "rem", "cal1")
        store.delete_config("cfg")
        assert store.list_configs() == ()
        conn = store._get_conn()
        assert conn.execute("SELECT COUNT(*) FROM remotes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM calendars").fetchone()[0] == 0


def test_remote_create_and_load_config_roundtrip(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("mycfg")
        remote = _make_remote("mycfg", "ncal")
        store.create_remote("mycfg", remote)
        store.create_calendar("mycfg", "ncal", "work-cal")
        store.create_calendar("mycfg", "ncal", "home-cal")

        snap = store.load_config("mycfg")
        assert snap == ConfigSnapshot(
            name="mycfg",
            remotes=(remote,),
            calendars=(
                CalendarRecord("mycfg", "ncal", "work-cal"),
                CalendarRecord("mycfg", "ncal", "home-cal"),
            ),
        )


def test_remote_delete_cascades_calendars(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        store.create_remote("cfg", _make_remote("cfg", "rem"))
        store.create_calendar("cfg", "rem", "cal1")
        store.delete_remote("cfg", "rem")
        conn = store._get_conn()
        assert conn.execute("SELECT COUNT(*) FROM calendars").fetchone()[0] == 0


def test_calendar_create_and_delete(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        store.create_remote("cfg", _make_remote("cfg", "rem"))
        store.create_calendar("cfg", "rem", "cal1")
        store.delete_calendar("cfg", "rem", "cal1")
        snap = store.load_config("cfg")
        assert snap.calendars == ()


def test_load_config_not_found(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        with pytest.raises(StoreNotFoundError, match="config not found"):
            store.load_config("nope")


def test_remote_not_found(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        with pytest.raises(StoreNotFoundError, match="remote not found"):
            store.delete_remote("cfg", "nope")


def test_calendar_not_found(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        store.create_remote("cfg", _make_remote("cfg", "rem"))
        with pytest.raises(StoreNotFoundError, match="calendar not found"):
            store.delete_calendar("cfg", "rem", "nope")


# ---------------------------------------------------------------------------
# 5. No-dots / charset / empty-name rejections + valid names
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,args",
    [
        ("create_config", ("a.b",)),
        ("delete_config", ("a.b",)),
    ],
)
def test_config_name_dot_rejected(method, args, tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        with pytest.raises(StoreValidationError, match="must not contain a dot"):
            getattr(store, method)(*args)


def test_config_name_empty_rejected(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        with pytest.raises(StoreValidationError, match="must not be empty"):
            store.create_config("")


def test_config_name_bad_charset_rejected(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        with pytest.raises(StoreValidationError, match="invalid characters"):
            store.create_config("a/b")


def test_config_name_leading_space_rejected(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        with pytest.raises(StoreValidationError, match="invalid characters"):
            store.create_config(" leading")


def test_remote_name_dot_rejected(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        with pytest.raises(StoreValidationError, match="must not contain a dot"):
            store.create_remote(
                "cfg",
                _make_remote("cfg", "x.y"),
            )


def test_calendar_name_dot_rejected(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        store.create_remote("cfg", _make_remote("cfg", "rem"))
        with pytest.raises(StoreValidationError, match="must not contain a dot"):
            store.create_calendar("cfg", "rem", "c.d")


def test_valid_hyphen_underscore_accepted(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("my-config")
        store.create_config("my_config")
        store.create_remote(
            "my-config",
            _make_remote("my-config", "remote-1"),
        )
        store.create_calendar("my-config", "remote-1", "cal_main")
        snap = store.load_config("my-config")
        assert snap.remotes[0].name == "remote-1"
        assert snap.calendars[0].name == "cal_main"


# ---------------------------------------------------------------------------
# 6. Direct-auth completeness
# ---------------------------------------------------------------------------


def test_direct_remote_empty_password_rejected(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        with pytest.raises(StoreValidationError, match="non-empty password_enc"):
            store.create_remote(
                "cfg",
                _make_remote("cfg", "rem", password_enc=""),
            )


def test_direct_remote_empty_username_rejected(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        with pytest.raises(StoreValidationError, match="non-empty username"):
            store.create_remote(
                "cfg",
                _make_remote("cfg", "rem", username=""),
            )


def test_direct_remote_empty_url_rejected(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        with pytest.raises(StoreValidationError, match="non-empty url"):
            store.create_remote(
                "cfg",
                _make_remote("cfg", "rem", url=""),
            )


def test_passthrough_remote_nonempty_username_rejected(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        with pytest.raises(StoreValidationError, match="empty username"):
            store.create_remote(
                "cfg",
                _make_remote(
                    "cfg",
                    "rem",
                    auth_mode="passthrough",
                    username="someone",
                    password_enc="",
                ),
            )


def test_passthrough_remote_nonempty_password_rejected(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        with pytest.raises(StoreValidationError, match="empty password_enc"):
            store.create_remote(
                "cfg",
                _make_remote(
                    "cfg",
                    "rem",
                    auth_mode="passthrough",
                    username="",
                    password_enc="something",
                ),
            )


def test_passthrough_remote_empty_url_rejected(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        with pytest.raises(StoreValidationError, match="non-empty url"):
            store.create_remote(
                "cfg",
                _make_remote(
                    "cfg",
                    "rem",
                    auth_mode="passthrough",
                    url="",
                    username="",
                    password_enc="",
                ),
            )


def test_valid_direct_remote_accepted(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        store.create_remote("cfg", _make_remote("cfg", "rem"))
        snap = store.load_config("cfg")
        assert snap.remotes[0].auth_mode == "direct"


def test_valid_passthrough_remote_accepted(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        store.create_remote(
            "cfg",
            _make_remote(
                "cfg",
                "rem",
                auth_mode="passthrough",
                url="https://passthrough.example.com",
                username="",
                password_enc="",
            ),
        )
        snap = store.load_config("cfg")
        assert snap.remotes[0].auth_mode == "passthrough"


# ---------------------------------------------------------------------------
# 7. Passthrough constraint — cases a–g
# ---------------------------------------------------------------------------


def _pt_remote(cfg, name="rem"):
    return _make_remote(
        cfg,
        name,
        auth_mode="passthrough",
        url="https://pt.example.com",
        username="",
        password_enc="",
    )


def test_pt_case_a_single_user_single_config_accepted(tmp_path):
    """7a: one user, one config, one passthrough remote → accepted."""
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        store.create_remote("cfg", _pt_remote("cfg"))
        store.create_user("alice", "h")
        store.set_user_configs("alice", ["cfg"])
        # No exception → pass.


def test_pt_case_b_second_passthrough_rejected_and_rolled_back(tmp_path):
    """7b: same user, second config with another passthrough → rejected,
    second config write rolled back (only first passthrough remains)."""
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("c1")
        store.create_config("c2")
        store.create_remote("c1", _pt_remote("c1"))
        store.create_user("alice", "h")
        store.set_user_configs("alice", ["c1", "c2"])
        with pytest.raises(StoreValidationError, match="at most one passthrough"):
            store.create_remote("c2", _pt_remote("c2", "rem2"))
        # c2 should have no passthrough remote after rollback.
        conn = store._get_conn()
        count = conn.execute("SELECT COUNT(*) FROM remotes WHERE config_name = 'c2'").fetchone()[0]
        assert count == 0


def test_pt_case_c_two_users_shared_config(tmp_path):
    """7c: user B has configs X + Y; Y already has a passthrough.  Adding a
    passthrough to X (shared by A) must check B's full set → rejected."""
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("X")
        store.create_config("Y")
        # Y already has a passthrough remote.
        store.create_remote("Y", _pt_remote("Y"))
        store.create_user("alice", "ha")
        store.create_user("bob", "hb")
        store.set_user_configs("alice", ["X"])
        store.set_user_configs("bob", ["X", "Y"])
        # Now try to add a passthrough to X — should fail because bob
        # would have two passthrough remotes (one in X, one in Y).
        with pytest.raises(StoreValidationError, match="at most one passthrough"):
            store.create_remote("X", _pt_remote("X"))
        # Verify rollback: X should have no passthrough remote.
        conn = store._get_conn()
        count = conn.execute("SELECT COUNT(*) FROM remotes WHERE config_name = 'X'").fetchone()[0]
        assert count == 0


def test_pt_case_d_update_remote_triggers_check(tmp_path):
    """7d: replacing a direct remote with a passthrough via update_remote
    triggers the same check."""
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("c1")
        store.create_config("c2")
        store.create_remote("c1", _pt_remote("c1"))
        store.create_user("alice", "h")
        store.set_user_configs("alice", ["c1", "c2"])
        # Add a direct remote to c2, then try to upgrade it to passthrough.
        store.create_remote("c2", _make_remote("c2", "rem2"))
        with pytest.raises(StoreValidationError, match="at most one passthrough"):
            store.update_remote("c2", "rem2", _pt_remote("c2", "rem2"))
        # Verify the remote stayed direct after rollback.
        snap = store.load_config("c2")
        assert snap.remotes[0].auth_mode == "direct"


def test_pt_case_e_remove_user_config_link_always_allowed(tmp_path):
    """7e: removing a user-config link is always allowed."""
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("c1")
        store.create_config("c2")
        store.create_remote("c1", _pt_remote("c1"))
        store.create_remote("c2", _pt_remote("c2", "rem2"))
        store.create_user("alice", "h")
        store.set_user_configs("alice", ["c1"])
        # Seed the second (poisoned) link with raw SQL: since M1 every
        # write path — including set_user_configs — enforces the
        # single-passthrough invariant, so the two-passthrough starting
        # state this test needs can only be built by bypassing the API.
        # Use a separate connection with an explicit commit so the seed
        # does not depend on the store connection's isolation settings.
        seed_conn = sqlite3.connect(_store_path(tmp_path))
        seed_conn.execute(
            "INSERT INTO user_configs (username, config_name) VALUES (?, ?)",
            ("alice", "c2"),
        )
        seed_conn.commit()
        seed_conn.close()
        # Now remove c2 — should not raise even though there are two
        # passthrough remotes across the configs (constraint is about the
        # user's reachable set; removing a config only loosens it).
        store.set_user_configs("alice", ["c1"])
        user = store.get_user("alice")
        assert user.config_names == ("c1",)


def test_pt_case_f_delete_passthrough_always_allowed(tmp_path):
    """7f: deleting a passthrough remote is always allowed."""
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("c1")
        store.create_remote("c1", _pt_remote("c1"))
        store.create_user("alice", "h")
        store.set_user_configs("alice", ["c1"])
        store.delete_remote("c1", "rem")
        snap = store.load_config("c1")
        assert snap.remotes == ()


def test_pt_case_g_set_user_configs_two_passthrough_rejected(tmp_path):
    """7g: replace-all via set_user_configs cannot produce a two-passthrough
    user — rejected inside the transaction and rolled back (state unchanged)."""
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("c1")
        store.create_config("c2")
        store.create_remote("c1", _pt_remote("c1"))
        store.create_remote("c2", _pt_remote("c2", "rem2"))
        store.create_user("alice", "h")
        store.set_user_configs("alice", ["c1"])
        with pytest.raises(StoreValidationError, match="at most one passthrough"):
            store.set_user_configs("alice", ["c1", "c2"])
        # Rollback: alice still linked to c1 only.
        user = store.get_user("alice")
        assert user.config_names == ("c1",)


# ---------------------------------------------------------------------------
# 8. StoreConflictError — duplicates / missing parent
# ---------------------------------------------------------------------------


def test_duplicate_config_raises(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        with pytest.raises(StoreConflictError, match="config already exists"):
            store.create_config("cfg")


def test_duplicate_remote_raises(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        store.create_remote("cfg", _make_remote("cfg", "rem"))
        with pytest.raises(StoreConflictError):
            store.create_remote("cfg", _make_remote("cfg", "rem"))


def test_duplicate_calendar_raises(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        store.create_remote("cfg", _make_remote("cfg", "rem"))
        store.create_calendar("cfg", "rem", "cal1")
        with pytest.raises(StoreConflictError):
            store.create_calendar("cfg", "rem", "cal1")


def test_remote_missing_parent_config_raises(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        with pytest.raises(StoreConflictError):
            store.create_remote(
                "nonexistent",
                _make_remote("nonexistent", "rem"),
            )


def test_calendar_missing_parent_remote_raises(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        with pytest.raises(StoreConflictError):
            store.create_calendar("cfg", "nonexistent", "cal1")


# ---------------------------------------------------------------------------
# 9. StoreNotFoundError
# ---------------------------------------------------------------------------


def test_get_user_not_found(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        with pytest.raises(StoreNotFoundError, match="user not found"):
            store.get_user("ghost")


def test_delete_config_not_found(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        with pytest.raises(StoreNotFoundError, match="config not found"):
            store.delete_config("ghost")


def test_delete_remote_not_found(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        with pytest.raises(StoreNotFoundError, match="remote not found"):
            store.delete_remote("cfg", "ghost")


def test_delete_calendar_not_found(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        store.create_remote("cfg", _make_remote("cfg", "rem"))
        with pytest.raises(StoreNotFoundError, match="calendar not found"):
            store.delete_calendar("cfg", "rem", "ghost")


def test_update_remote_not_found(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        with pytest.raises(StoreNotFoundError, match="remote not found"):
            store.update_remote("cfg", "ghost", _make_remote("cfg", "ghost"))


# ---------------------------------------------------------------------------
# 10. FrozenInstanceError on record mutation
# ---------------------------------------------------------------------------


def test_user_record_frozen(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_user("alice", "h")
        user = store.get_user("alice")
        with pytest.raises(dataclasses.FrozenInstanceError):
            user.username = "bob"  # type: ignore[misc]


def test_remote_record_frozen(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        store.create_remote("cfg", _make_remote("cfg", "rem"))
        snap = store.load_config("cfg")
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.remotes[0].name = "changed"  # type: ignore[misc]


def test_calendar_record_frozen(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        store.create_remote("cfg", _make_remote("cfg", "rem"))
        store.create_calendar("cfg", "rem", "cal1")
        snap = store.load_config("cfg")
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.calendars[0].name = "changed"  # type: ignore[misc]


def test_config_snapshot_frozen(tmp_path):
    with ConfigStore(_store_path(tmp_path)) as store:
        store.create_config("cfg")
        snap = store.load_config("cfg")
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.name = "changed"  # type: ignore[misc]
