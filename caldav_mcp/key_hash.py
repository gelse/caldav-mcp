"""API-key hashing utilities (PBKDF2-HMAC-SHA256).

Extracted from :mod:`caldav_mcp.config_cli` (Step M4.2) so that the auth
module can verify DB-stored key hashes without importing CLI code.

Storage format::

    pbkdf2_sha256$600000$<salt_b64>$<hash_b64>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_HASH_ITERATIONS = 600_000
_SALT_BYTES = 16
_HASH_BYTES = 32
_HASH_PREFIX = "pbkdf2_sha256"


def hash_api_key(key: str) -> str:
    """Hash an API key with PBKDF2-HMAC-SHA256.

    Returns a self-describing string::

        pbkdf2_sha256$600000$<salt_b64>$<hash_b64>

    Two calls with the same key produce different salts (different hashes)
    but both verify True via :func:`verify_api_key`.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(
        "sha256", key.encode("utf-8"), salt, _HASH_ITERATIONS, dklen=_HASH_BYTES
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    dk_b64 = base64.b64encode(dk).decode("ascii")
    return f"{_HASH_PREFIX}${_HASH_ITERATIONS}${salt_b64}${dk_b64}"


def verify_api_key(key: str, stored: str) -> bool:
    """Verify *key* against a *stored* hash string.

    Uses constant-time comparison.  Returns ``False`` on malformed stored
    strings — never raises.
    """
    try:
        parts = stored.split("$")
        if len(parts) != 4:
            return False
        prefix, iterations_str, salt_b64, hash_b64 = parts
        if prefix != _HASH_PREFIX:
            return False
        iterations = int(iterations_str)
        if iterations <= 0:
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        if not salt or not expected:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", key.encode("utf-8"), salt, iterations, dklen=len(expected)
        )
    except (ValueError, TypeError, OverflowError):
        return False
    return hmac.compare_digest(dk, expected)
