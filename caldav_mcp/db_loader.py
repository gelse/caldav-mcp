"""Pro-mode DB loader — assembles the M2 singleton shape from the M3 store.

This module reads the SQLite configuration store **once** at startup and
produces an :class:`~caldav_mcp.app_config.AppConfig` in ``"db"`` mode
plus a tuple of :class:`ProUser` snapshots for downstream auth (M4.2+).

**Design invariants**

* The loader runs exactly once at server startup.  Beyond creating an
  empty store (schema only) when the file is missing but its parent
  directory exists and is writable, the loader never writes to the store;
  configuration changes require a restart.  The admin API is the server's
  store writer, users only.
* No env-var reads inside this module — the master secret is passed
  explicitly via the *secret* parameter so that callers (and tests) control
  the key material.
* No validation of its own — it inherits M3 store guarantees (no-dots
  names, direct-auth completeness, at-most-one-passthrough) for free.
"""

from __future__ import annotations

import base64
import hashlib
import logging
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

logger = logging.getLogger(__name__)

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


def _check_store_path(db_path: str) -> bool:
    """Validate that *db_path* is an accessible regular file — or creatable.

    An explicit ``os.stat`` probe replaces a blind existence check so that
    permission failures (e.g. a bind-mounted store directory the container
    user cannot traverse) are never reported as "store not found".  The
    mapping — each with a distinct, actionable message carrying the path,
    the running uid/gid and concrete remedies, never secrets:

    * ``""`` / ``":memory:"`` → "not a persistent file" (SQLite's in-memory
      and unnamed targets would silently recreate an empty database on
      every open — never acceptable for the shared store),
    * ``FileNotFoundError`` + missing parent directory → "store not found"
      (parent directories are never created),
    * ``FileNotFoundError`` + unwritable parent directory →
      "permission denied: cannot create",
    * ``FileNotFoundError`` + writable parent directory → returns ``True``
      (caller creates an empty store),
    * ``PermissionError``    → "permission denied" (inaccessible parent dir),
    * directory at the path  → "is a directory, not a SQLite database file",
    * file not readable      → "permission denied" (file mode),
    * any other ``OSError``  → message including the errno.

    Returns
    -------
    bool
        ``True`` when the store file is absent but its parent directory
        exists and is writable (auto-create case); ``False`` when the file
        exists and passed every check.

    Raises
    ------
    ProConfigError
        On any of the failure classes above.
    """
    if not db_path or db_path == ":memory:":
        raise ProConfigError(
            f"Pro-mode config store path {db_path!r} is not a persistent file: "
            f"pro mode requires a real store file — an in-memory or unnamed "
            f"database is recreated empty on every open and would silently "
            f"discard users and configs. Fix: point CALDAV_MCP_DB_PATH at a "
            f"file path whose parent directory exists and is writable."
        )
    try:
        st = os.stat(db_path)
    except FileNotFoundError as exc:
        parent = os.path.dirname(os.path.abspath(db_path))
        if not os.path.isdir(parent):
            raise ProConfigError(
                f"Pro-mode config store not found: {db_path!r} — parent directory "
                f"{parent!r} does not exist (uid={os.getuid()} gid={os.getgid()}). "
                f"Fix: create the directory or correct CALDAV_MCP_DB_PATH — the store "
                f"is only auto-created when its parent directory already exists."
            ) from exc
        if not os.access(parent, os.W_OK | os.X_OK):
            raise ProConfigError(
                f"Pro-mode config store permission denied: cannot create {db_path!r} "
                f"as uid={os.getuid()} gid={os.getgid()}: parent directory {parent!r} "
                f"is not writable. "
                f"Fix: run the container as the store owner via compose "
                f'`user: "<uid>:<gid>"`, or `chown -R 100:101 <dir>` so the store '
                f"matches the image's app user (uid=100 gid=101)."
            ) from exc
        return True
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
    return False


def load_pro_state(db_path: str, secret: str) -> ProState:
    """Read the SQLite store and assemble an ``AppConfig`` + user snapshot.

    When *db_path* does not exist but its parent directory exists and is
    writable, an empty store (schema only, no users/configs) is created
    and a WARNING explains how to populate it; every other missing or
    unusable path shape stays a typed :class:`ProConfigError`.

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
        When the store's parent directory is missing or not writable (the
        store is only auto-created when the file is absent and its parent
        directory already exists and is writable), or the file exists but
        is unreadable (permission denied on the file or a parent
        directory), is not a regular file, or its schema version is newer
        than the code supports.
    ConfigSecretError
        When decryption of any stored password fails (wrong secret).
    """
    store_missing = _check_store_path(db_path)

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

    if store_missing:
        # Fresh deployment: an empty store (schema only) was just created.
        # Startup continues with zero users/configs — MCP auth rejects
        # everyone until users exist; the admin API (or CLI) creates them.
        logger.warning(
            "Pro-mode config store %r did not exist — created an empty store with "
            "no users and no configs. Populate configs, remotes and calendars with "
            "the `caldav-mcp-config` CLI (changes apply after a restart); add users "
            "via the admin REST API (requires CALDAV_MCP_ADMIN_KEY set at startup; "
            "user changes apply live) or the CLI (user changes apply after a "
            "restart).",
            db_path,
        )

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
