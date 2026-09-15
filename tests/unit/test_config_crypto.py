"""Unit tests for caldav_mcp.config_crypto — credential encryption layer."""

from __future__ import annotations

import importlib
import secrets

import pytest
from cryptography.fernet import Fernet

from caldav_mcp.config_crypto import (
    ConfigSecretError,
    decrypt_secret,
    encrypt_secret,
    load_master_key,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def secret_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set CALDAV_MCP_CONFIG_SECRET to a fresh random value for the test."""
    value = secrets.token_urlsafe(32)
    monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", value)
    return value


# ---------------------------------------------------------------------------
# 1. Round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_ascii(self, secret_env: str) -> None:
        assert decrypt_secret(encrypt_secret("s3cret")) == "s3cret"

    def test_unicode_umlauts(self, secret_env: str) -> None:
        plaintext = "Ünïcödé Müller-straße"
        assert decrypt_secret(encrypt_secret(plaintext)) == plaintext

    def test_empty_string(self, secret_env: str) -> None:
        assert decrypt_secret(encrypt_secret("")) == ""

    def test_long_1kiB_string(self, secret_env: str) -> None:
        plaintext = "x" * 1024
        assert decrypt_secret(encrypt_secret(plaintext)) == plaintext


# ---------------------------------------------------------------------------
# 2. Ciphertext differs but both decrypt
# ---------------------------------------------------------------------------


class TestRandomIV:
    def test_two_encryptions_differ(self, secret_env: str) -> None:
        t1 = encrypt_secret("same")
        t2 = encrypt_secret("same")
        assert t1 != t2
        assert decrypt_secret(t1) == "same"
        assert decrypt_secret(t2) == "same"


# ---------------------------------------------------------------------------
# 3. load_master_key raises on missing / empty / whitespace secrets
# ---------------------------------------------------------------------------


class TestLoadMasterKeyErrors:
    @pytest.mark.parametrize(
        "env_value",
        [
            pytest.param(None, id="unset"),
            pytest.param("", id="empty"),
            pytest.param("   ", id="whitespace-only"),
        ],
    )
    def test_raises_config_secret_error(
        self, monkeypatch: pytest.MonkeyPatch, env_value: str | None
    ) -> None:
        if env_value is None:
            monkeypatch.delenv("CALDAV_MCP_CONFIG_SECRET", raising=False)
        else:
            monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", env_value)
        with pytest.raises(ConfigSecretError):
            load_master_key()


# ---------------------------------------------------------------------------
# 4. Deterministic derivation
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_secret_same_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", "my-secret")
        k1 = load_master_key()
        k2 = load_master_key()
        assert k1 == k2

    def test_different_secrets_different_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", "secret-a")
        k1 = load_master_key()
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", "secret-b")
        k2 = load_master_key()
        assert k1 != k2


# ---------------------------------------------------------------------------
# 5. Wrong-key decrypt
# ---------------------------------------------------------------------------


class TestWrongKey:
    def test_decrypt_with_wrong_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", "key-A")
        token = encrypt_secret("payload")
        monkeypatch.setenv("CALDAV_MCP_CONFIG_SECRET", "key-B")
        with pytest.raises(ConfigSecretError):
            decrypt_secret(token)


# ---------------------------------------------------------------------------
# 6. Garbage input
# ---------------------------------------------------------------------------


class TestGarbageInput:
    @pytest.mark.parametrize(
        "bad_token",
        [
            pytest.param("not-a-token", id="plain-text"),
            pytest.param("gAAAA", id="truncated-fernet"),
            pytest.param("", id="empty-string"),
        ],
    )
    def test_raises_config_secret_error(self, secret_env: str, bad_token: str) -> None:
        with pytest.raises(ConfigSecretError):
            decrypt_secret(bad_token)


# ---------------------------------------------------------------------------
# 7. Token validity — Fernet can decrypt directly
# ---------------------------------------------------------------------------


class TestFernetCompatibility:
    def test_manual_fernet_decrypts(self, secret_env: str) -> None:
        token = encrypt_secret("check")
        key = load_master_key()
        plaintext = Fernet(key).decrypt(token.encode()).decode("utf-8")
        assert plaintext == "check"


# ---------------------------------------------------------------------------
# 8. Import safety (no env var set)
# ---------------------------------------------------------------------------


class TestImportSafety:
    def test_import_without_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CALDAV_MCP_CONFIG_SECRET", raising=False)
        # Re-import to verify no side effects at import time.
        import caldav_mcp.config_crypto as mod

        importlib.reload(mod)
        # Module loaded successfully — no exception raised.
