"""Unit tests for caldav_mcp.config_store (SQLite configuration store).

Uses pytest's ``tmp_path`` fixture with a real SQLite database on disk.
All tests follow the plain-function style of tests/unit/test_auth.py.
"""

from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from caldav_mcp.config_store import (
    SCHEMA_VERSION,
    CalendarRecord,
    ConfigSnapshot,
    ConfigStore,
    RemoteRecord,
    StoreConflictError,
    StoreNotFoundError,
    StoreSchemaError,
    StoreValidationError,
    UserRecord,
)

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
# 7. Passthrough constraint — cases a–f
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
        store.set_user_configs("alice", ["c1", "c2"])
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
