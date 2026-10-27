"""Tests for configuration validation (Finding 6.3)."""

import logging
import os
import sys
from types import SimpleNamespace
from unittest import mock

import pytest
from pydantic import ValidationError

from caldav_mcp.config_schema import CalDAVConfig, ServerConfig, load_server_config


class TestServerConfig:
    def test_valid_port(self):
        cfg = ServerConfig(port=8080)
        assert cfg.port == 8080

    def test_port_zero_invalid(self):
        with pytest.raises(ValidationError, match="Port must be between"):
            ServerConfig(port=0)

    def test_port_too_large(self):
        with pytest.raises(ValidationError, match="Port must be between"):
            ServerConfig(port=70000)

    def test_port_boundaries(self):
        assert ServerConfig(port=1).port == 1
        assert ServerConfig(port=65535).port == 65535

    def test_path_must_start_with_slash(self):
        with pytest.raises(ValidationError, match="must start with '/'"):
            ServerConfig(path="mcp")

    def test_valid_path(self):
        assert ServerConfig(path="/mcp").path == "/mcp"

    def test_empty_tz_is_valid(self):
        cfg = ServerConfig(tz="")
        assert cfg.tz == ""

    def test_valid_timezone(self):
        cfg = ServerConfig(tz="Europe/Vienna")
        assert cfg.tz == "Europe/Vienna"

    def test_invalid_timezone(self):
        with pytest.raises(ValidationError, match="Unknown timezone"):
            ServerConfig(tz="Not/A/Timezone")


class TestCalDAVConfig:
    def test_empty_url_valid(self):
        cfg = CalDAVConfig(url="")
        assert cfg.url == ""

    def test_valid_https_url(self):
        cfg = CalDAVConfig(url="https://cloud.example.com/dav/")
        assert cfg.url == "https://cloud.example.com/dav/"

    def test_valid_http_url(self):
        cfg = CalDAVConfig(url="http://localhost:5232/")
        assert cfg.url == "http://localhost:5232/"

    def test_ftp_scheme_invalid(self):
        with pytest.raises(ValidationError, match="http or https"):
            CalDAVConfig(url="ftp://example.com/dav/")

    def test_no_hostname_invalid(self):
        with pytest.raises(ValidationError, match="hostname"):
            CalDAVConfig(url="https://")


class TestDBConfigEnabled:
    """Tests for DB_CONFIG_ENABLED startup validation (Step M4.1)."""

    def test_missing_db_path_raises_value_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_CONFIG_ENABLED", "true")
        monkeypatch.delenv("CALDAV_MCP_DB_PATH", raising=False)
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", "some-secret")
        with pytest.raises(ValueError, match="CALDAV_MCP_DB_PATH"):
            load_server_config()

    def test_missing_config_secret_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DB_CONFIG_ENABLED", "true")
        monkeypatch.setenv("CALDAV_MCP_DB_PATH", "/tmp/store.db")
        monkeypatch.delenv("CALDAV_MCP_CONFIG_SECRET", raising=False)
        with pytest.raises(ValueError, match="CALDAV_MCP_CONFIG_SECRET"):
            load_server_config()

    def test_both_missing_raises_naming_both(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_CONFIG_ENABLED", "true")
        monkeypatch.delenv("CALDAV_MCP_DB_PATH", raising=False)
        monkeypatch.delenv("CALDAV_MCP_CONFIG_SECRET", raising=False)
        with pytest.raises(ValueError, match="CALDAV_MCP_DB_PATH.*CALDAV_MCP_CONFIG_SECRET"):
            load_server_config()

    def test_empty_db_path_after_strip_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_CONFIG_ENABLED", "true")
        monkeypatch.setenv("CALDAV_MCP_DB_PATH", "   ")
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", "some-secret")
        with pytest.raises(ValueError, match="CALDAV_MCP_DB_PATH"):
            load_server_config()

    def test_both_set_loads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_CONFIG_ENABLED", "true")
        monkeypatch.setenv("CALDAV_MCP_DB_PATH", "/tmp/store.db")
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", "some-secret")
        # Should not raise
        cfg = load_server_config()
        assert cfg.port == 8080

    def test_disabled_no_requirement(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DB_CONFIG_ENABLED", raising=False)
        monkeypatch.delenv("CALDAV_MCP_DB_PATH", raising=False)
        monkeypatch.delenv("CALDAV_MCP_CONFIG_SECRET", raising=False)
        # Should not raise — DB vars not required when DB_CONFIG_ENABLED is false
        cfg = load_server_config()
        assert cfg.port == 8080

    def test_false_value_no_requirement(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_CONFIG_ENABLED", "false")
        monkeypatch.delenv("CALDAV_MCP_DB_PATH", raising=False)
        monkeypatch.delenv("CALDAV_MCP_CONFIG_SECRET", raising=False)
        cfg = load_server_config()
        assert cfg.port == 8080


# ---------------------------------------------------------------------------
# CALDAV_MCP_ADMIN_KEY (issue-16 milestone M2.2)
# ---------------------------------------------------------------------------


def _reload_admin_key(env_val: str | None) -> str:
    """Re-import ``caldav_mcp.config`` with a controlled ``CALDAV_MCP_ADMIN_KEY``.

    Returns the resulting ``ADMIN_KEY`` constant.  ``sys.modules`` is restored
    in a ``finally`` block so no cross-test pollution occurs (pattern mirrors
    ``test_read_only_mode._reload_config``).
    """
    to_remove = [k for k in sys.modules if k.startswith("caldav_mcp")]
    saved = {k: sys.modules[k] for k in to_remove}
    for k in to_remove:
        del sys.modules[k]

    try:
        if env_val is None:
            # Ensure the var is truly absent.
            patched = {k: v for k, v in os.environ.items() if k != "CALDAV_MCP_ADMIN_KEY"}
        else:
            patched = {**os.environ, "CALDAV_MCP_ADMIN_KEY": env_val}

        with mock.patch.dict(os.environ, patched, clear=True):
            from caldav_mcp import config as _cfg  # noqa: E402

            return _cfg.ADMIN_KEY
    finally:
        # Restore all caldav_mcp modules to the pre-test state.
        for k in [k for k in sys.modules if k.startswith("caldav_mcp")]:
            del sys.modules[k]
        sys.modules.update(saved)


class TestAdminKey:
    """``CALDAV_MCP_ADMIN_KEY`` plumbing (issue-16 milestone M2.2)."""

    def test_server_config_admin_key_field_default(self) -> None:
        """The ``admin_key`` field defaults to ``''`` on the model itself."""
        assert ServerConfig().admin_key == ""

    def test_load_server_config_default_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unset env var → ``load_server_config().admin_key == ''``."""
        monkeypatch.delenv("CALDAV_MCP_ADMIN_KEY", raising=False)
        cfg = load_server_config()
        assert cfg.admin_key == ""

    def test_load_server_config_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The env var is read through to ``admin_key``."""
        monkeypatch.setenv("CALDAV_MCP_ADMIN_KEY", "test-admin-key")
        cfg = load_server_config()
        assert cfg.admin_key == "test-admin-key"

    def test_load_server_config_empty_env_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty env var stays empty (no implicit sentinel)."""
        monkeypatch.setenv("CALDAV_MCP_ADMIN_KEY", "")
        cfg = load_server_config()
        assert cfg.admin_key == ""

    @pytest.mark.parametrize(
        ("env_val", "expected"),
        [
            (None, ""),
            ("", ""),
            ("test-admin-key", "test-admin-key"),
        ],
        ids=["unset", "empty", "set"],
    )
    def test_config_module_admin_key(self, env_val: str | None, expected: str) -> None:
        """``caldav_mcp.config.ADMIN_KEY`` defaults to ``''`` and follows the env."""
        assert _reload_admin_key(env_val) == expected


def _run_main_simple_mode(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    admin_key: str,
) -> list[str]:
    """Run ``server.main()`` down the simple-mode path with side effects mocked.

    Returns the WARNING-level log messages captured during the call.  The HTTP
    server launch, the app-config singleton, and the pro/simple branch flags
    are patched so the call is side-effect free.
    """
    import server
    from caldav_mcp import app_config, config

    monkeypatch.delenv("DB_CONFIG_ENABLED", raising=False)
    monkeypatch.setenv("CALDAV_MCP_ADMIN_KEY", admin_key)

    app_stub = SimpleNamespace(mode="header")
    with (
        mock.patch.object(config, "ADMIN_KEY", admin_key),
        mock.patch.object(config, "DB_CONFIG_ENABLED", False),
        mock.patch.object(app_config, "load_app_config", return_value=object()),
        mock.patch.object(app_config, "configure_app_config"),
        mock.patch.object(app_config, "get_app_config", return_value=app_stub),
        mock.patch.object(server.mcp, "run_http_async", new=mock.AsyncMock()),
        caplog.at_level(logging.WARNING, logger="caldav-mcp"),
    ):
        server.main()
    return [record.getMessage() for record in caplog.records]


class TestAdminKeyStartupWarning:
    """``server.main()`` warns about ``CALDAV_MCP_ADMIN_KEY`` outside pro mode (M2.2)."""

    def test_warns_when_admin_key_set(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A set ADMIN_KEY with DB_CONFIG_ENABLED false logs one warning."""
        messages = _run_main_simple_mode(monkeypatch, caplog, "super-secret-admin-key")
        admin_warnings = [m for m in messages if "CALDAV_MCP_ADMIN_KEY" in m]
        assert len(admin_warnings) == 1
        assert "ignored" in admin_warnings[0]
        assert "pro mode" in admin_warnings[0]

    def test_warns_without_echoing_key_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The key value itself never appears in any log message (no-secrets rule)."""
        secret = "super-secret-admin-key-do-not-log"
        messages = _run_main_simple_mode(monkeypatch, caplog, secret)
        for message in messages:
            assert secret not in message

    def test_no_warning_when_admin_key_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No CALDAV_MCP_ADMIN_KEY warning when the key is empty."""
        messages = _run_main_simple_mode(monkeypatch, caplog, "")
        assert not any("CALDAV_MCP_ADMIN_KEY" in m for m in messages)


# ---------------------------------------------------------------------------
# Pro-mode store writability warning (startup, never fatal)
# ---------------------------------------------------------------------------


def _run_main_pro_mode(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    *,
    admin_key: str,
    db_path: str,
) -> tuple[list[str], mock.AsyncMock]:
    """Run ``server.main()`` down the pro-mode path with side effects mocked.

    Mirrors ``_run_main_simple_mode``: the HTTP launch, the config singleton,
    the loader, and the admin-route registration are patched so the call is
    side-effect free.  Returns ``(WARNING-level log messages, run spy)``.
    """
    import server
    from caldav_mcp import admin, app_config, auth, config, db_loader

    monkeypatch.setenv("DB_CONFIG_ENABLED", "true")
    monkeypatch.setenv("CALDAV_MCP_DB_PATH", db_path)
    monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", "unit-test-secret")
    monkeypatch.delenv("SHOW_UI", raising=False)
    monkeypatch.delenv("CALDAV_URL", raising=False)

    pro_stub = SimpleNamespace(
        app_config=SimpleNamespace(mode="db", config=None, configs=()),
        users=(),
    )
    with (
        mock.patch.object(config, "ADMIN_KEY", admin_key),
        mock.patch.object(config, "DB_CONFIG_ENABLED", True),
        mock.patch.object(config, "SHOW_UI", False),
        mock.patch.object(server, "API_KEY", ""),
        mock.patch.object(db_loader, "load_pro_state", return_value=pro_stub),
        mock.patch.object(app_config, "configure_app_config"),
        mock.patch.object(auth, "configure_pro_users"),
        mock.patch.object(admin, "configure_admin_routes") as admin_routes_spy,
        mock.patch.object(server.mcp, "run_http_async", new=mock.AsyncMock()) as run_spy,
        caplog.at_level(logging.WARNING, logger="caldav-mcp"),
    ):
        server.main()
    assert admin_routes_spy.called or not admin_key
    return [record.getMessage() for record in caplog.records], run_spy


_IS_ROOT = getattr(os, "geteuid", lambda: -1)() == 0


class TestProStoreWritableWarning:
    """Admin API enabled + non-writable store → startup warning, never a failure."""

    @pytest.mark.skipif(_IS_ROOT, reason="root bypasses filesystem permission checks")
    def test_warns_when_store_not_writable(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Read-only store file with admin key set → one actionable warning."""
        db = tmp_path / "store.db"
        db.write_text("placeholder")
        db.chmod(0o444)
        try:
            messages, run_spy = _run_main_pro_mode(
                monkeypatch, caplog, admin_key="test-admin-key", db_path=str(db)
            )
        finally:
            db.chmod(0o644)

        warnings = [m for m in messages if "not writable" in m]
        assert len(warnings) == 1, f"expected exactly one writability warning: {messages}"
        assert str(db) in warnings[0]
        assert f"uid={os.getuid()}" in warnings[0]
        assert f"gid={os.getgid()}" in warnings[0]
        # Never fatal: startup continued to the HTTP launch.
        run_spy.assert_called_once()

    def test_no_warning_when_store_writable(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Writable store → no writability warning."""
        db = tmp_path / "store.db"
        db.write_text("placeholder")

        messages, _ = _run_main_pro_mode(
            monkeypatch, caplog, admin_key="test-admin-key", db_path=str(db)
        )
        assert not any("not writable" in m for m in messages)

    def test_warning_does_not_echo_admin_key(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """No-secrets rule: the admin key value never appears in any message."""
        secret = "super-secret-admin-key-do-not-log"
        db = tmp_path / "store.db"
        db.write_text("placeholder")
        db.chmod(0o444)
        try:
            messages, _ = _run_main_pro_mode(monkeypatch, caplog, admin_key=secret, db_path=str(db))
        finally:
            db.chmod(0o644)
        for message in messages:
            assert secret not in message


# ---------------------------------------------------------------------------
# SHOW_UI (milestone UI-M3, design (a) + (d))
# ---------------------------------------------------------------------------


def _reload_show_ui(env_val: str | None) -> bool:
    """Re-import ``caldav_mcp.config`` with a controlled ``SHOW_UI`` env value.

    Returns the resulting ``SHOW_UI`` constant.  ``sys.modules`` is restored
    in a ``finally`` block so no cross-test pollution occurs (pattern mirrors
    ``test_read_only_mode._reload_config``).
    """
    to_remove = [k for k in sys.modules if k.startswith("caldav_mcp")]
    saved = {k: sys.modules[k] for k in to_remove}
    for k in to_remove:
        del sys.modules[k]

    try:
        if env_val is None:
            patched = {k: v for k, v in os.environ.items() if k != "SHOW_UI"}
        else:
            patched = {**os.environ, "SHOW_UI": env_val}

        with mock.patch.dict(os.environ, patched, clear=True):
            from caldav_mcp import config as _cfg  # noqa: E402

            return _cfg.SHOW_UI
    finally:
        for k in [k for k in sys.modules if k.startswith("caldav_mcp")]:
            del sys.modules[k]
        sys.modules.update(saved)


class TestShowUIConfig:
    """``SHOW_UI`` env plumbing (UI-M3 design (a))."""

    def test_server_config_show_ui_field_default(self) -> None:
        assert ServerConfig().show_ui is False

    def test_load_server_config_default_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SHOW_UI", raising=False)
        assert load_server_config().show_ui is False

    @pytest.mark.parametrize(
        "val",
        ["true", "True", "TRUE", "1", "yes", "YES"],
        ids=lambda v: f"val={v!r}",
    )
    def test_load_server_config_trueish(self, val: str, monkeypatch: pytest.MonkeyPatch) -> None:
        """Case-insensitive true/1/yes triple → show_ui True."""
        monkeypatch.setenv("SHOW_UI", val)
        assert load_server_config().show_ui is True

    @pytest.mark.parametrize(
        "val",
        ["false", "False", "0", "no", "", "garbage", "nope"],
        ids=lambda v: f"val={v!r}",
    )
    def test_load_server_config_falseish(self, val: str, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHOW_UI", val)
        assert load_server_config().show_ui is False

    @pytest.mark.parametrize(
        "val",
        ["true", "True", "TRUE", "1", "yes", "YES"],
        ids=lambda v: f"val={v!r}",
    )
    def test_config_module_show_ui_trueish(self, val: str) -> None:
        assert _reload_show_ui(val) is True

    @pytest.mark.parametrize(
        "val",
        ["false", "False", "0", "no", "", "garbage"],
        ids=lambda v: f"val={v!r}",
    )
    def test_config_module_show_ui_falseish(self, val: str) -> None:
        assert _reload_show_ui(val) is False

    def test_config_module_show_ui_default_false(self) -> None:
        assert _reload_show_ui(None) is False


def _run_main_show_ui(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    show_ui: bool,
) -> tuple[list[str], mock.MagicMock]:
    """Run ``server.main()`` with ``SHOW_UI`` controlled and the UI spy armed.

    Mirrors ``_run_main_simple_mode``: the HTTP launch, the app-config
    singleton, and the pro/simple branch flags are patched.  The spy
    *replaces* ``configure_ui_routes`` so the shared ``server.mcp`` singleton
    is never mutated (its real registration/fail-fast path is covered by
    ``tests/unit/test_ui_static.py``); the gate's job is only the
    called/never-called contract.  Returns ``(log messages, spy)``.
    """
    import server
    from caldav_mcp import app_config, config, ui_static

    monkeypatch.delenv("DB_CONFIG_ENABLED", raising=False)
    monkeypatch.delenv("CALDAV_MCP_ADMIN_KEY", raising=False)

    app_stub = SimpleNamespace(mode="header")
    with (
        mock.patch.object(ui_static, "configure_ui_routes") as spy,
        mock.patch.object(config, "SHOW_UI", show_ui),
        mock.patch.object(config, "ADMIN_KEY", ""),
        mock.patch.object(config, "DB_CONFIG_ENABLED", False),
        mock.patch.object(app_config, "load_app_config", return_value=object()),
        mock.patch.object(app_config, "configure_app_config"),
        mock.patch.object(app_config, "get_app_config", return_value=app_stub),
        mock.patch.object(server.mcp, "run_http_async", new=mock.AsyncMock()),
        caplog.at_level(logging.INFO, logger="caldav-mcp"),
    ):
        server.main()
    return [record.getMessage() for record in caplog.records], spy


class TestShowUIGate:
    """``server.main()`` registers the /ui routes iff ``SHOW_UI`` (design (d))."""

    def test_configure_called_once_when_show_ui_true(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """SHOW_UI true → configure_ui_routes called exactly once with the shared mcp."""
        import server

        messages, spy = _run_main_show_ui(monkeypatch, caplog, True)
        spy.assert_called_once_with(server.mcp)
        assert any("UI enabled at /ui (SHOW_UI=true)" in m for m in messages)

    def test_configure_never_called_when_show_ui_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """SHOW_UI false (default) → the routes are never registered."""
        messages, spy = _run_main_show_ui(monkeypatch, caplog, False)
        spy.assert_not_called()
        assert not any("UI enabled at /ui" in m for m in messages)
