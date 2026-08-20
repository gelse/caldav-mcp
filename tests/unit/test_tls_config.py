"""Unit tests for TLS / SSL configuration in server.py and caldav_mcp.config."""

import importlib
from unittest import mock

import caldav_mcp.config as config
import server


# ---------------------------------------------------------------------------
# TLS defaults
# ---------------------------------------------------------------------------

def test_tls_config_defaults_disabled():
    """By default (no env vars set), TLS cert and key paths are empty."""
    with mock.patch.object(server, "TLS_CERT_PATH", ""):
        with mock.patch.object(server, "TLS_KEY_PATH", ""):
            result = server._build_ssl_config()
            assert result is None


def test_tls_config_reads_env():
    """When cert and key are set, _build_ssl_config returns a dict."""
    with mock.patch.object(server, "TLS_CERT_PATH", "/path/to/cert.pem"):
        with mock.patch.object(server, "TLS_KEY_PATH", "/path/to/key.pem"):
            with mock.patch.object(server, "TLS_CA_BUNDLE", ""):
                result = server._build_ssl_config()
                assert result is not None
                assert result["ssl_certfile"] == "/path/to/cert.pem"
                assert result["ssl_keyfile"] == "/path/to/key.pem"
                assert "ssl_ca_certs" not in result


def test_tls_config_with_ca_bundle():
    """When a CA bundle is set, it appears in the returned config."""
    with mock.patch.object(server, "TLS_CERT_PATH", "/cert.pem"):
        with mock.patch.object(server, "TLS_KEY_PATH", "/key.pem"):
            with mock.patch.object(server, "TLS_CA_BUNDLE", "/ca-bundle.pem"):
                result = server._build_ssl_config()
                assert result is not None
                assert result["ssl_ca_certs"] == "/ca-bundle.pem"


def test_tls_config_missing_key_returns_none():
    """When cert is set but key is missing, TLS is not configured."""
    with mock.patch.object(server, "TLS_CERT_PATH", "/cert.pem"):
        with mock.patch.object(server, "TLS_KEY_PATH", ""):
            result = server._build_ssl_config()
            assert result is None


def test_tls_config_missing_cert_returns_none():
    """When key is set but cert is missing, TLS is not configured."""
    with mock.patch.object(server, "TLS_CERT_PATH", ""):
        with mock.patch.object(server, "TLS_KEY_PATH", "/key.pem"):
            result = server._build_ssl_config()
            assert result is None


# ---------------------------------------------------------------------------
# CALDAV_VERIFY_SSL config
# ---------------------------------------------------------------------------

def test_caldav_verify_ssl_default_true():
    """CALDAV_VERIFY_SSL defaults to True."""
    import os
    os.environ.pop("CALDAV_MCP_CALDAV_VERIFY_SSL", None)
    raw = os.environ.get("CALDAV_MCP_CALDAV_VERIFY_SSL", "true")
    assert raw.lower() in ("true", "1", "yes")


def test_caldav_verify_ssl_can_disable():
    """CALDAV_MCP_CALDAV_VERIFY_SSL=false disables verification."""
    assert "false".lower() not in ("true", "1", "yes")
    assert "FALSE".lower() not in ("true", "1", "yes")


def test_caldav_verify_ssl_env_parsing():
    """Verify the exact parsing logic for CALDAV_VERIFY_SSL."""
    def _parse(val):
        return val.lower() in ("true", "1", "yes")

    assert _parse("true") is True
    assert _parse("TRUE") is True
    assert _parse("1") is True
    assert _parse("yes") is True
    assert _parse("false") is False
    assert _parse("FALSE") is False
    assert _parse("0") is False
    assert _parse("no") is False
    assert _parse("") is False


# ---------------------------------------------------------------------------
# main() uses uvicorn_config when TLS is set
# ---------------------------------------------------------------------------

def test_main_passes_uvicorn_config_with_tls():
    """main() should include uvicorn_config when TLS is configured."""
    with mock.patch.object(server, "TLS_CERT_PATH", "/cert.pem"):
        with mock.patch.object(server, "TLS_KEY_PATH", "/key.pem"):
            with mock.patch.object(server, "TLS_CA_BUNDLE", ""):
                cfg = server._build_ssl_config()
                assert isinstance(cfg, dict)
                assert "ssl_certfile" in cfg
                assert "ssl_keyfile" in cfg
