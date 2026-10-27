"""Tests for caldav_mcp.db_loader — pro-mode DB loader (Step M4.1)."""

from __future__ import annotations

import errno
import logging
import os
import sqlite3
from pathlib import Path

import pytest

from caldav_mcp.app_config import (
    AppConfig,
    Config,
    Remote,
    find_calendar,
    find_config,
    find_remote,
    implicit_remote,
)
from caldav_mcp.config_crypto import ConfigSecretError, encrypt_secret
from caldav_mcp.config_store import SCHEMA_VERSION, ConfigStore, RemoteRecord
from caldav_mcp.db_loader import ProConfigError, ProState, ProUser, load_pro_state

SECRET = "test-master-secret-for-loading-1234"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_store(db_path: str, *, with_passthrough: bool = False) -> None:
    """Populate a ConfigStore with test data.

    Creates one config "work", two remotes (one direct, optionally one
    passthrough), two calendars per remote, and two users.
    """
    with ConfigStore(db_path) as store:
        store.create_config("work")
        store.create_remote(
            "work",
            RemoteRecord(
                config_name="work",
                name="nextcloud",
                url="https://nc.example.com/dav/",
                auth_mode="direct",
                username="alice",
                password_enc=encrypt_secret("s3cret-pwd"),
            ),
        )
        store.create_calendar("work", "nextcloud", "personal")
        store.create_calendar("work", "nextcloud", "team")

        if with_passthrough:
            store.create_remote(
                "work",
                RemoteRecord(
                    config_name="work",
                    name="radicale",
                    url="https://rad.example.com/",
                    auth_mode="passthrough",
                    username="",
                    password_enc="",
                ),
            )
            store.create_calendar("work", "radicale", "shared")

        store.create_user("alice", "hash-alice")
        store.create_user("bob", "hash-bob")
        store.grant_config("alice", "work")
        store.grant_config("bob", "work")


# ---------------------------------------------------------------------------
# Case 1: Round-trip — direct remote with encrypted password
# ---------------------------------------------------------------------------


class TestRoundTripDirect:
    def test_direct_remote_decrypts_calendars_and_users(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = str(tmp_path / "store.db")
        # encrypt_secret reads env, so patch it for setup
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", SECRET)
        _seed_store(db)

        # load_pro_state takes the secret explicitly — no env patching needed
        state = load_pro_state(db, SECRET)

        assert isinstance(state, ProState)
        assert state.app_config.mode == "db"
        assert state.app_config.config is None

        # One config in configs
        assert len(state.app_config.configs) == 1
        cfg = state.app_config.configs[0]
        assert cfg.name == "work"

        # Direct remote: decrypted password
        assert len(cfg.remotes) == 1
        remote = cfg.remotes[0]
        assert remote.name == "nextcloud"
        assert remote.auth_mode == "direct"
        assert remote.url == "https://nc.example.com/dav/"
        assert remote.username == "alice"
        assert remote.password == "s3cret-pwd"

        # Calendars in order
        assert len(cfg.calendars) == 1
        rname, cals = cfg.calendars[0]
        assert rname == "nextcloud"
        assert len(cals) == 2
        assert cals[0].name == "personal"
        assert cals[1].name == "team"

        # Users
        assert len(state.users) == 2
        assert state.users[0] == ProUser(
            username="alice", key_hash="hash-alice", config_names=("work",)
        )
        assert state.users[1] == ProUser(
            username="bob", key_hash="hash-bob", config_names=("work",)
        )


# ---------------------------------------------------------------------------
# Case 2: Passthrough remote mapping
# ---------------------------------------------------------------------------


class TestPassthroughRemote:
    def test_passthrough_remote_has_empty_credentials(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = str(tmp_path / "store.db")
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", SECRET)
        _seed_store(db, with_passthrough=True)

        state = load_pro_state(db, SECRET)
        cfg = state.app_config.configs[0]

        # Two remotes
        assert len(cfg.remotes) == 2
        direct = cfg.remotes[0]
        passthrough = cfg.remotes[1]

        assert direct.auth_mode == "direct"
        assert direct.password == "s3cret-pwd"

        assert passthrough.name == "radicale"
        assert passthrough.auth_mode == "passthrough"
        assert passthrough.url == "https://rad.example.com/"
        assert passthrough.username == ""
        assert passthrough.password == ""

        # Calendars include both remotes
        assert len(cfg.calendars) == 2
        rname1, cals1 = cfg.calendars[0]
        rname2, cals2 = cfg.calendars[1]
        assert rname1 == "nextcloud"
        assert rname2 == "radicale"
        assert len(cals1) == 2
        assert len(cals2) == 1
        assert cals2[0].name == "shared"


# ---------------------------------------------------------------------------
# Case 3: Wrong secret → ConfigSecretError propagates
# ---------------------------------------------------------------------------


class TestWrongSecret:
    def test_wrong_secret_raises_config_secret_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db = str(tmp_path / "store.db")
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", SECRET)
        _seed_store(db)

        with pytest.raises(ConfigSecretError):
            load_pro_state(db, "wrong-secret")


# ---------------------------------------------------------------------------
# Case 4: Unusable store path (missing parent / bad content) → ProConfigError
# ---------------------------------------------------------------------------


class TestMissingStore:
    def test_too_new_schema_version_raises_pro_config_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Schema-version-too-new store → ProConfigError wrapping StoreSchemaError."""
        db = str(tmp_path / "bad_schema.db")
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", SECRET)
        # Create a valid store first, then tamper the schema version.
        with ConfigStore(db) as store:
            store.create_config("test")

        conn = sqlite3.connect(db)
        conn.execute("UPDATE schema_version SET version = 999")
        conn.commit()
        conn.close()

        with pytest.raises(ProConfigError, match="bad_schema.db"):
            load_pro_state(db, SECRET)

    def test_non_sqlite_file_raises_pro_config_error(self, tmp_path: Path) -> None:
        """A file that is not a valid SQLite database → ProConfigError."""
        db = str(tmp_path / "not_a_db.db")
        # Write garbage to the file so it exists but isn't a valid SQLite DB.
        Path(db).write_text("this is not a sqlite database")

        with pytest.raises(ProConfigError):
            load_pro_state(db, SECRET)

    def test_nonexistent_path_raises_pro_config_error(self, tmp_path: Path) -> None:
        """Missing store whose parent directory does not exist → ProConfigError;
        neither the store file nor the missing parent directory is created."""
        db = str(tmp_path / "missing_dir" / "does_not_exist.db")

        with pytest.raises(ProConfigError, match="Pro-mode config store not found"):
            load_pro_state(db, SECRET)

        assert not Path(db).exists(), "load_pro_state must not create the store file"
        assert not (tmp_path / "missing_dir").exists(), (
            "load_pro_state must not create missing parent directories"
        )


# ---------------------------------------------------------------------------
# Case 5: AppConfig helpers — hit/miss + implicit_remote raises in db mode
# ---------------------------------------------------------------------------


class TestAppConfigHelpers:
    def _db_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfig:
        db = str(tmp_path / "store.db")
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", SECRET)
        _seed_store(db, with_passthrough=True)
        state = load_pro_state(db, SECRET)
        return state.app_config

    def test_find_config_hit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        app = self._db_config(tmp_path, monkeypatch)
        cfg = find_config(app, "work")
        assert cfg is not None
        assert cfg.name == "work"

    def test_find_config_miss(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        app = self._db_config(tmp_path, monkeypatch)
        assert find_config(app, "nonexistent") is None

    def test_find_remote_hit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        app = self._db_config(tmp_path, monkeypatch)
        cfg = find_config(app, "work")
        assert cfg is not None
        remote = find_remote(cfg, "nextcloud")
        assert remote is not None
        assert remote.name == "nextcloud"
        assert remote.auth_mode == "direct"

    def test_find_remote_miss(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        app = self._db_config(tmp_path, monkeypatch)
        cfg = find_config(app, "work")
        assert cfg is not None
        assert find_remote(cfg, "nonexistent") is None

    def test_find_calendar_hit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        app = self._db_config(tmp_path, monkeypatch)
        cfg = find_config(app, "work")
        assert cfg is not None
        cal = find_calendar(cfg, "nextcloud", "personal")
        assert cal is not None
        assert cal.name == "personal"

    def test_find_calendar_miss(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        app = self._db_config(tmp_path, monkeypatch)
        cfg = find_config(app, "work")
        assert cfg is not None
        assert find_calendar(cfg, "nextcloud", "nonexistent") is None
        assert find_calendar(cfg, "nonexistent", "personal") is None

    def test_implicit_remote_raises_in_db_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = self._db_config(tmp_path, monkeypatch)
        with pytest.raises(ValueError, match="db mode"):
            implicit_remote(app)


# ---------------------------------------------------------------------------
# Case 6: Backward compatibility — AppConfig(mode="env", ...) without configs
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_env_mode_configs_default_empty(self) -> None:
        app = AppConfig(
            mode="env",
            config=Config(
                name="default",
                remotes=(
                    Remote(
                        name="default",
                        url="https://nc.example.com/dav/",
                        auth_mode="direct",
                        username="u",
                        password="p",
                    ),
                ),
                calendars=(),
            ),
        )
        assert app.configs == ()

    def test_header_mode_configs_default_empty(self) -> None:
        app = AppConfig(mode="header", config=None)
        assert app.configs == ()


# ---------------------------------------------------------------------------
# Case 7: Store-path access errors → distinct, actionable ProConfigError
# ---------------------------------------------------------------------------

_IS_ROOT = getattr(os, "geteuid", lambda: -1)() == 0


def _assert_permission_denied_message(msg: str, path: str) -> None:
    """Shared contract for permission failures: actionable, never "not found"."""
    assert "permission denied" in msg.lower(), f"message must say permission denied: {msg!r}"
    assert "not found" not in msg.lower(), f"permission failure must not say not found: {msg!r}"
    assert path in msg, f"message must include the store path {path!r}: {msg!r}"
    assert f"uid={os.getuid()}" in msg, f"message must include the running uid: {msg!r}"
    assert f"gid={os.getgid()}" in msg, f"message must include the running gid: {msg!r}"
    # Remedies required by the bugfix contract.
    assert "user:" in msg, f"message must mention the compose user: remedy: {msg!r}"
    assert "chown -R 100:101" in msg, f"message must mention the chown remedy: {msg!r}"


class TestStorePathAccess:
    """Pro-mode store path probing maps errno → distinct ProConfigError messages.

    Reproduction tests for the bind-mount permission bug: a store beneath a
    directory the process cannot traverse (or a file it cannot read) must be
    reported as *permission denied* with uid/gid and remedies — never as the
    misleading "store not found" produced by a swallowed ``OSError``.
    """

    @pytest.mark.skipif(_IS_ROOT, reason="root bypasses filesystem permission checks")
    def test_untraversable_parent_dir_raises_permission_denied(self, tmp_path: Path) -> None:
        """chmod 000 parent directory → permission-denied message, not "not found"."""
        locked = tmp_path / "locked"
        locked.mkdir()
        db = str(locked / "store.db")
        locked.chmod(0o000)
        try:
            with pytest.raises(ProConfigError) as excinfo:
                load_pro_state(db, SECRET)
        finally:
            locked.chmod(0o700)  # let pytest clean up tmp_path

        _assert_permission_denied_message(str(excinfo.value), db)

    def test_stat_permission_error_maps_to_permission_denied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Root-safe variant: os.stat raising PermissionError → same message."""
        db = str(tmp_path / "store.db")
        Path(db).write_text("placeholder")

        real_stat = os.stat

        def deny_stat(path, *args, **kwargs):  # type: ignore[no-untyped-def]
            if os.fspath(path) == db:
                raise PermissionError(errno.EACCES, "Permission denied", os.fspath(path))
            return real_stat(path, *args, **kwargs)

        monkeypatch.setattr(os, "stat", deny_stat)

        with pytest.raises(ProConfigError) as excinfo:
            load_pro_state(db, SECRET)

        _assert_permission_denied_message(str(excinfo.value), db)

    def test_missing_parent_dir_still_reports_not_found(self, tmp_path: Path) -> None:
        """ENOENT with a missing parent keeps the "store not found" contract
        (the store is only auto-created when the parent directory exists)."""
        db = str(tmp_path / "gone" / "store.db")

        with pytest.raises(ProConfigError) as excinfo:
            load_pro_state(db, SECRET)

        msg = str(excinfo.value)
        assert "not found" in msg, f"missing parent must keep the not-found message: {msg!r}"
        assert "permission denied" not in msg.lower()
        assert db in msg
        assert f"uid={os.getuid()}" in msg, f"message must include the running uid: {msg!r}"
        assert f"gid={os.getgid()}" in msg, f"message must include the running gid: {msg!r}"

    def test_store_path_directory_raises_distinct_message(self, tmp_path: Path) -> None:
        """A directory at the store path gets a directory message (not not-found)."""
        d = tmp_path / "store.db"
        d.mkdir()

        with pytest.raises(ProConfigError) as excinfo:
            load_pro_state(str(d), SECRET)

        msg = str(excinfo.value)
        assert "directory" in msg.lower(), f"message must say directory: {msg!r}"
        assert "not found" not in msg.lower()
        assert "permission denied" not in msg.lower()
        assert str(d) in msg

    @pytest.mark.skipif(_IS_ROOT, reason="root bypasses filesystem permission checks")
    def test_unreadable_store_file_raises_permission_denied(self, tmp_path: Path) -> None:
        """chmod 000 file (parent traversable) → permission-denied naming the path."""
        db = tmp_path / "store.db"
        db.write_text("placeholder")
        db.chmod(0o000)
        try:
            with pytest.raises(ProConfigError) as excinfo:
                load_pro_state(str(db), SECRET)
        finally:
            db.chmod(0o600)

        _assert_permission_denied_message(str(excinfo.value), str(db))

    def test_unreadable_file_maps_via_access_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Root-safe variant: os.access(…, R_OK) False → permission-denied message."""
        db = str(tmp_path / "store.db")
        Path(db).write_text("placeholder")

        real_access = os.access

        def deny_read(path, mode, *args, **kwargs):  # type: ignore[no-untyped-def]
            if os.fspath(path) == db and mode & os.R_OK:
                return False
            return real_access(path, mode, *args, **kwargs)

        monkeypatch.setattr(os, "access", deny_read)

        with pytest.raises(ProConfigError) as excinfo:
            load_pro_state(db, SECRET)

        _assert_permission_denied_message(str(excinfo.value), db)

    def test_other_oserror_includes_errno(self, tmp_path: Path) -> None:
        """ENAMETOOLONG-class OSError → distinct message including errno."""
        db = str(tmp_path / ("x" * 300))  # component > NAME_MAX → OSError, not ENOENT

        with pytest.raises(ProConfigError) as excinfo:
            load_pro_state(db, SECRET)

        msg = str(excinfo.value)
        assert "errno=" in msg, f"message must include errno: {msg!r}"
        assert "not found" not in msg.lower(), f"other OSError must not say not found: {msg!r}"

    def test_non_regular_file_raises_distinct_message(self, tmp_path: Path) -> None:
        """A FIFO at the store path → distinct message, never handed to SQLite."""
        fifo = tmp_path / "store.db"
        os.mkfifo(fifo)

        with pytest.raises(ProConfigError) as excinfo:
            load_pro_state(str(fifo), SECRET)

        msg = str(excinfo.value)
        assert "not a regular file" in msg, f"expected non-regular-file text: {msg!r}"
        assert str(fifo) in msg
        assert "not found" not in msg.lower()
        assert "permission denied" not in msg.lower()

    def test_readonly_store_still_loads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A mode-0444 store loads fine (SQLite read-only fallback) — the startup
        writability warning fires *after* such a successful load, never instead
        of it.  Works for root too (root ignores the mode)."""
        db = str(tmp_path / "store.db")
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", SECRET)
        _seed_store(db)
        Path(db).chmod(0o444)
        try:
            state = load_pro_state(db, SECRET)
        finally:
            Path(db).chmod(0o644)

        assert state.app_config.configs[0].name == "work"


# ---------------------------------------------------------------------------
# Case 8: First-start auto-create — missing file + existing writable parent
# ---------------------------------------------------------------------------


class TestAutoCreateEmptyStore:
    """Pro mode creates an empty store on first start.

    Contract: missing store file + parent directory exists and is writable →
    an empty store (schema only) is created, startup continues, and a WARNING
    on the loader's module logger says the store was created (with its path),
    that it holds no users/configs, and how to populate it.  Missing or
    unwritable parents are never created — they keep typed, actionable
    :class:`ProConfigError` failures.
    """

    def test_missing_file_with_writable_parent_creates_empty_store_and_warns(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Fresh path (no extension — mirrors the reported Docker scenario
        ``CALDAV_MCP_DB_PATH=/data/store``) → created, empty, warned."""
        db = str(tmp_path / "store")

        with caplog.at_level(logging.WARNING, logger="caldav_mcp.db_loader"):
            state = load_pro_state(db, SECRET)

        assert Path(db).is_file(), "load_pro_state must create the missing store file"

        # Empty ProState: no configs, no users.
        assert state.app_config.mode == "db"
        assert state.app_config.configs == ()
        assert state.users == ()

        # Schema present: expected version row, user/config tables, zero rows.
        conn = sqlite3.connect(db)
        try:
            version = conn.execute("SELECT version FROM schema_version").fetchone()
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        finally:
            conn.close()
        assert version == (SCHEMA_VERSION,)
        assert {"schema_version", "users", "configs"} <= tables
        assert user_count == (0,)

        # Warning contract: created store + path + no-users + population guidance.
        loader_warnings = [
            r.getMessage()
            for r in caplog.records
            if r.name == "caldav_mcp.db_loader" and r.levelno == logging.WARNING
        ]
        assert loader_warnings, "expected a WARNING about the created store"
        text = "\n".join(loader_warnings)
        assert "created an empty store" in text, f"warning must say the store was created: {text!r}"
        assert db in text, f"warning must include the store path: {text!r}"
        assert "no users" in text, f"warning must state the store has no users: {text!r}"
        # How to populate: CLI for configs/remotes/calendars; admin API or CLI for users.
        assert "caldav-mcp-config" in text, f"warning must name the CLI: {text!r}"
        assert "admin REST API" in text, f"warning must mention the admin API: {text!r}"
        # Never log secret material.
        assert SECRET not in caplog.text, "warning must never contain the config secret"

    def test_existing_store_loads_without_creation_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """An existing valid store loads unchanged — no auto-create warning."""
        db = str(tmp_path / "store.db")
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", SECRET)
        _seed_store(db)

        with caplog.at_level(logging.WARNING, logger="caldav_mcp.db_loader"):
            state = load_pro_state(db, SECRET)

        assert len(state.users) == 2
        assert state.app_config.configs[0].name == "work"
        created_warnings = [r for r in caplog.records if "created an empty store" in r.getMessage()]
        assert not created_warnings, "existing store must not emit the auto-create warning"

    def test_memory_store_path_raises_pro_config_error(self) -> None:
        """SQLite's ':memory:' (and the unnamed temporary target) would open a
        fresh empty database on every connection — users/configs written then
        silently lost.  Both are rejected before the auto-create probe."""
        with pytest.raises(ProConfigError, match="persistent"):
            load_pro_state(":memory:", SECRET)
        with pytest.raises(ProConfigError, match="persistent"):
            load_pro_state("", SECRET)

    def test_missing_parent_dir_raises_and_creates_nothing(self, tmp_path: Path) -> None:
        """Missing parent directory → actionable ProConfigError; the loader
        never creates parent directories (or the file beneath one)."""
        db = str(tmp_path / "no_such_dir" / "store")

        with pytest.raises(ProConfigError, match="not found") as excinfo:
            load_pro_state(db, SECRET)

        msg = str(excinfo.value)
        assert db in msg
        assert "auto-created" in msg, f"message must explain the auto-create rule: {msg!r}"
        assert not (tmp_path / "no_such_dir").exists(), "loader must not create the parent dir"
        assert not Path(db).exists()

    def test_unwritable_parent_raises_permission_denied_and_creates_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Root-safe variant: os.access denying W_OK on the parent → the same
        actionable permission-denied contract as #39, nothing created."""
        parent = tmp_path / "store-dir"
        parent.mkdir()
        db = str(parent / "store")

        real_access = os.access

        def deny_write(path, mode, *args, **kwargs):  # type: ignore[no-untyped-def]
            if os.fspath(path) == str(parent) and mode & os.W_OK:
                return False
            return real_access(path, mode, *args, **kwargs)

        monkeypatch.setattr(os, "access", deny_write)

        with pytest.raises(ProConfigError) as excinfo:
            load_pro_state(db, SECRET)

        _assert_permission_denied_message(str(excinfo.value), db)
        assert "cannot create" in str(excinfo.value)
        assert not Path(db).exists(), "loader must not create the store file"
