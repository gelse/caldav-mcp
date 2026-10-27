"""Pro-mode DB loader — assembles the M2 singleton shape from the M3 store.

This module reads the SQLite configuration store **once** at startup and
produces an :class:`~caldav_mcp.app_config.AppConfig` in ``"db"`` mode
plus a tuple of :class:`ProUser` snapshots for downstream auth (M4.2+).

**Design invariants**

* The loader runs exactly once at server startup.  The loader never writes
  to the store; configuration changes require a restart.  The admin API is
  the server's only store writer, users only.
* No env-var reads inside this module — the master secret is passed
  explicitly via the *secret* parameter so that callers (and tests) control
  the key material.
* No validation of its own — it inherits M3 store guarantees (no-dots
  names, direct-auth completeness, at-most-one-passthrough) for free.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

from caldav_mcp.app_config import AppConfig, Calendar, Config, Remote
from caldav_mcp.config_crypto import ConfigSecretError
from caldav_mcp.config_store import (
    ConfigSnapshot,
    ConfigStore,
    RemoteRecord,
    StoreError,
)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProUser:
    """Snapshot of a pro-mode user from the store."""

    username: str
    key_hash: str  # opaque here; verified by M4.2
    config_names: tuple[str, ...]


@dataclass(frozen=True)
class ProState:
    """Aggregated result of :func:`load_pro_state`."""

    app_config: AppConfig  # mode="db", configs populated
    users: tuple[ProUser, ...]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _derive_master_key(secret: str) -> bytes:
    """Derive a 32-byte Fernet key from the master *secret* (SHA-256 → b64)."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _decrypt_with_secret(token: str, secret: str) -> str:
    """Decrypt a Fernet *token* using *secret* as the master key.

    Mirrors :func:`caldav_mcp.config_crypto.decrypt_secret` but takes the
    secret explicitly instead of reading from the environment.
    """
    key = _derive_master_key(secret)
    try:
        return Fernet(key).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ConfigSecretError(
            "Decryption failed. The token is invalid or the "
            "master secret has likely been changed since encryption."
        ) from exc


def _map_remote(rec: RemoteRecord, secret: str) -> Remote:
    """Map a :class:`RemoteRecord` to an :class:`Remote`.

    For ``"direct"`` remotes the encrypted password is decrypted with the
    master *secret*.  For ``"passthrough"`` remotes username and password
    stay empty (per-request headers supply them).
    """
    if rec.auth_mode == "direct":
        password = _decrypt_with_secret(rec.password_enc, secret)
        return Remote(
            name=rec.name,
            url=rec.url,
            auth_mode="direct",
            username=rec.username,
            password=password,
        )

    # passthrough
    return Remote(
        name=rec.name,
        url=rec.url,
        auth_mode="passthrough",
        username="",
        password="",
    )


def _map_config(snapshot: ConfigSnapshot, secret: str) -> Config:
    """Map a :class:`ConfigSnapshot` to a :class:`Config`."""
    remotes = tuple(_map_remote(r, secret) for r in snapshot.remotes)

    # Group calendars by remote_name → (remote_name, (Calendar, ...))
    groups: dict[str, list[Calendar]] = {}
    for cal_rec in snapshot.calendars:
        groups.setdefault(cal_rec.remote_name, []).append(Calendar(name=cal_rec.name))

    # Preserve remote ordering for calendar groups.
    remote_names = [r.name for r in snapshot.remotes]
    calendars = tuple(
        (rname, tuple(groups.get(rname, ()))) for rname in remote_names if rname in groups
    )

    return Config(name=snapshot.name, remotes=remotes, calendars=calendars)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ProConfigError(Exception):
    """Raised when the pro-mode store cannot be loaded or decrypted.

    The message includes the database path for diagnostics but never
    secrets.
    """


def _check_store_path(db_path: str) -> None:
    """Validate that *db_path* points at an accessible regular file.

    An explicit ``os.stat`` probe replaces a blind existence check so that
    permission failures (e.g. a bind-mounted store directory the container
    user cannot traverse) are never reported as "store not found".  The
    mapping — each with a distinct, actionable message carrying the path,
    the running uid/gid and concrete remedies, never secrets:

    * ``FileNotFoundError``  → "store not found" (original ENOENT contract),
    * ``PermissionError``    → "permission denied" (inaccessible parent dir),
    * directory at the path  → "is a directory, not a SQLite database file",
    * file not readable      → "permission denied" (file mode),
    * any other ``OSError``  → message including the errno.

    Raises
    ------
    ProConfigError
        On any of the failure classes above.
    """
    try:
        st = os.stat(db_path)
    except FileNotFoundError as exc:
        raise ProConfigError(f"Pro-mode config store not found: {db_path!r}") from exc
    except PermissionError as exc:
        raise ProConfigError(
            f"Pro-mode config store permission denied: cannot access {db_path!r} as "
            f"uid={os.getuid()} gid={os.getgid()} "
            f"({exc.strerror or exc}; a parent directory is likely not traversable). "
            f"Fix: run the container as the store owner via compose "
            f'`user: "<uid>:<gid>"`, or `chown -R 100:101 <dir>` so the store '
            f"matches the image's app user (uid=100 gid=101)."
        ) from exc
    except OSError as exc:
        raise ProConfigError(
            f"Cannot access pro-mode config store {db_path!r}: {exc.strerror or exc} "
            f"(errno={exc.errno}) — check the path and its parent directory/mount."
        ) from exc

    if stat.S_ISDIR(st.st_mode):
        raise ProConfigError(
            f"Pro-mode config store path is a directory, not a SQLite database file: {db_path!r}"
        )
    if not stat.S_ISREG(st.st_mode):
        # Device/FIFO/socket at the path — never hand these to SQLite (the
        # pre-stat is_file() check used to reject every non-regular file).
        raise ProConfigError(f"Pro-mode config store path is not a regular file: {db_path!r}")
    if not os.access(db_path, os.R_OK):
        raise ProConfigError(
            f"Pro-mode config store permission denied: cannot read {db_path!r} as "
            f"uid={os.getuid()} gid={os.getgid()} (file mode denies read access). "
            f"Fix: run the container as the store owner via compose "
            f'`user: "<uid>:<gid>"`, or `chown -R 100:101 <dir>` so the store '
            f"matches the image's app user (uid=100 gid=101)."
        )


def load_pro_state(db_path: str, secret: str) -> ProState:
    """Read the SQLite store and assemble an ``AppConfig`` + user snapshot.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite configuration database.
    secret:
        Master secret used to derive the Fernet decryption key.  Passed
        explicitly — this function does **not** read from the environment.

    Raises
    ------
    ProConfigError
        When the store file is missing, unreadable (permission denied on
        the file or a parent directory), not a regular file, or its schema
        version is newer than the code supports.
    ConfigSecretError
        When decryption of any stored password fails (wrong secret).
    """
    _check_store_path(db_path)

    try:
        with ConfigStore(db_path) as store:
            config_names = store.list_configs()
            configs: list[Config] = []
            for name in config_names:
                snapshot = store.load_config(name)
                configs.append(_map_config(snapshot, secret))

            user_records = store.list_users()
    except StoreError as exc:
        raise ProConfigError(f"Failed to load pro-mode config from {db_path!r}: {exc}") from exc
    except ConfigSecretError:
        # Re-raise ConfigSecretError without wrapping — the caller needs
        # the typed exception for startup fail-fast behavior.
        raise
    except Exception as exc:
        raise ProConfigError(
            f"Unexpected error loading pro-mode config from {db_path!r}: {exc}"
        ) from exc

    users = tuple(
        ProUser(
            username=u.username,
            key_hash=u.key_hash,
            config_names=u.config_names,
        )
        for u in user_records
    )

    app_config = AppConfig(mode="db", config=None, configs=tuple(configs))
    return ProState(app_config=app_config, users=users)
