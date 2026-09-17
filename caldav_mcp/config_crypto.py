"""Credential encryption layer for CalDAV config store.

Provides Fernet-based authenticated encryption of CalDAV passwords at rest.
The master key is derived from the ``CALDAV_MCP_CONFIG_SECRET`` environment
variable via SHA-256 — the input must be a high-entropy deployment secret, not
a low-entropy password (no KDF stretching is applied). Changing the secret
invalidates all stored ciphertexts.

Both helpers call :func:`load_master_key` internally so the key is never cached
at module level — this keeps the module import-only-safe with no side effects.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


class ConfigSecretError(Exception):
    """Raised when the master secret is missing or decryption fails."""


def load_master_key() -> bytes:
    """Read ``CALDAV_MCP_CONFIG_SECRET`` and derive a 32-byte Fernet key.

    The raw secret is hashed with SHA-256 and url-safe-base64-encoded to
    produce the 32-byte key that Fernet requires.

    Raises:
        ConfigSecretError: If the env var is unset or empty after stripping.

    Note:
        This assumes a high-entropy deployment secret. No password-based key
        derivation function (KDF) stretch is applied because the threat model
        is an operator-provided secret, not a user password.
    """
    raw = os.environ.get("CALDAV_MCP_CONFIG_SECRET")
    if raw is None or raw.strip() == "":
        raise ConfigSecretError(
            "CALDAV_MCP_CONFIG_SECRET is not set or is empty. "
            "Provide a high-entropy secret for credential encryption."
        )
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt *plaintext* with the master key; return a Fernet token.

    Each call produces a different token (random IV embedded by Fernet).
    """
    key = load_master_key()
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Decrypt a Fernet *token* with the master key.

    Raises:
        ConfigSecretError: If the token is invalid, corrupted, or encrypted
            with a different master key.
    """
    key = load_master_key()
    try:
        return Fernet(key).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ConfigSecretError(
            "Decryption failed. The token is invalid or the "
            "CALDAV_MCP_CONFIG_SECRET has likely been changed since encryption."
        ) from exc
