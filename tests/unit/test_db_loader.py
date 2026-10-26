"""Tests for caldav_mcp.db_loader — pro-mode DB loader (Step M4.1)."""

from __future__ import annotations

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
from caldav_mcp.config_store import ConfigStore, RemoteRecord
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
# Case 4: Missing store file → ProConfigError
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
        import sqlite3

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

    def test_nonexistent_path_raises_pro_config_error_and_does_not_create_file(
        self, tmp_path: Path
    ) -> None:
        """Non-existent store path → ProConfigError; no file is created."""
        db = str(tmp_path / "does_not_exist.db")

        with pytest.raises(ProConfigError, match="Pro-mode config store not found"):
            load_pro_state(db, SECRET)

        assert not Path(db).exists(), "load_pro_state must not create the store file"


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
