"""Tests for configuration validation (Finding 6.3)."""

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
