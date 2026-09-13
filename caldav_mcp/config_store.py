"""SQLite-backed configuration store for caldav-mcp.

This module provides the persistent configuration store used in **pro mode**
(M3+).  It is the *only* writer of the on-disk database; the CLI (M3.3)
creates users, configs, remotes and calendars through this API.  Reads
happen at startup only, in the future M4 loader that assembles the
read-only ``caldav_mcp.app_config`` singleton from the rows stored here.

Data-shape mapping
------------------
The frozen dataclasses exposed by this module (``UserRecord``,
``RemoteRecord``, ``CalendarRecord``, ``ConfigSnapshot``) map **1 : 1** onto
the M2 ``Config`` / ``Remote`` / ``Calendar`` frozen dataclasses defined in
``caldav_mcp/app_config.py``.  The M4 loader performs the final assembly;
this module guarantees field correspondence only.

**This module must NOT import** ``caldav_mcp/app_config.py`` — it may not
exist yet when M3.1 lands independently.

Design decisions
----------------
* **No-dots rule** – config, remote and calendar names must not contain
  ``.`` (dot).  The future dotted-path addressing used by the MCP tool
  layer (``config.remote.calendar``) would become ambiguous if a component
  itself contained a dot.  The charset is intentionally conservative:
  ``^[A-Za-z0-9][A-Za-z0-9_-]*$``.  Usernames are exempt from the
  charset rule but must be non-empty (they are free-form identifiers
  chosen by the CLI user).
* **Passthrough constraint** – at most **one** passthrough remote may be
  reachable by any given user across *all* of their configs.  A passthrough
  remote injects request-time credentials (the MCP client's CalDAV headers);
  if two passthrough remotes were reachable, the server could not decide
  which credential set to forward (rationale: idea doc lines 56–58).

Row ordering
------------
``remotes`` and ``calendars`` are returned in ``rowid`` order (i.e.
insertion order).  No separate ``seq`` column is used; the M4 loader and
any caller relying on deterministic ordering should treat the implicit
``rowid`` ordering as canonical.

Store lifecycle
---------------
The store is opened via ``ConfigStore(path)`` which is a context manager.
On open the module runs ``PRAGMA foreign_keys = ON`` and applies the DDL
(idempotent ``CREATE TABLE IF NOT EXISTS``).  A forward-compatibility guard
rejects databases whose ``schema_version`` row is *newer* than the version
this code knows (currently **1**).

All multi-statement writes use an explicit transaction (``BEGIN``/``COMMIT``
or ``ROLLBACK``) via ``isolation_level=None``.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class StoreError(Exception):
    """Base exception for all store errors."""


class StoreValidationError(StoreError):
    """Raised for business-rule violations (no-dots, passthrough, direct-auth)."""


class StoreConflictError(StoreError):
    """Raised on duplicate names or FK violations (mapped from IntegrityError)."""


class StoreNotFoundError(StoreError):
    """Raised when a requested row does not exist."""


class StoreSchemaError(StoreError):
    """Raised when the on-disk schema version is newer than this code supports."""


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UserRecord:
    """Row record for a user."""

    username: str
    key_hash: str
    config_names: tuple[str, ...]


@dataclass(frozen=True)
class RemoteRecord:
    """Row record for a remote within a config."""

    config_name: str
    name: str
    url: str
    auth_mode: Literal["direct", "passthrough"]
    username: str  # "" for passthrough
    password_enc: str  # opaque; "" for passthrough


@dataclass(frozen=True)
class CalendarRecord:
    """Row record for a calendar within a remote."""

    config_name: str
    remote_name: str
    name: str


@dataclass(frozen=True)
class ConfigSnapshot:
    """Aggregate snapshot returned by :meth:`ConfigStore.load_config`."""

    name: str
    remotes: tuple[RemoteRecord, ...]
    calendars: tuple[CalendarRecord, ...]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

SCHEMA_VERSION = 1

_DDL = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    username     TEXT PRIMARY KEY,
    key_hash     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_configs (
    username     TEXT NOT NULL REFERENCES users(username) ON DELETE CASCADE,
    config_name  TEXT NOT NULL REFERENCES configs(name)    ON DELETE CASCADE,
    PRIMARY KEY (username, config_name)
);

CREATE TABLE IF NOT EXISTS configs (
    name         TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS remotes (
    config_name  TEXT NOT NULL REFERENCES configs(name) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    url          TEXT NOT NULL,
    auth_mode    TEXT NOT NULL CHECK (auth_mode IN ('direct', 'passthrough')),
    username     TEXT NOT NULL DEFAULT '',
    password_enc TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (config_name, name)
);

CREATE TABLE IF NOT EXISTS calendars (
    config_name  TEXT NOT NULL,
    remote_name  TEXT NOT NULL,
    name         TEXT NOT NULL,
    PRIMARY KEY (config_name, remote_name, name),
    FOREIGN KEY (config_name, remote_name)
        REFERENCES remotes(config_name, name) ON DELETE CASCADE
);
"""


def _validate_name(value: str, label: str) -> None:
    """Enforce no-dots, non-empty, and charset rules on config/remote/calendar names."""
    if not value:
        raise StoreValidationError(f"{label} must not be empty")
    if "." in value:
        raise StoreValidationError(f"{label} must not contain a dot: {value!r}")
    if not _NAME_RE.match(value):
        raise StoreValidationError(
            f"{label} contains invalid characters: {value!r} (must match {_NAME_RE.pattern})"
        )


def _validate_username(username: str) -> None:
    """Usernames must be non-empty but are exempt from charset/dot rules."""
    if not username:
        raise StoreValidationError("username must not be empty")


def _validate_remote_auth(remote: RemoteRecord) -> None:
    """Enforce direct-auth completeness and passthrough emptiness."""
    if remote.auth_mode == "direct":
        if not remote.url:
            raise StoreValidationError("direct remote must have a non-empty url")
        if not remote.username:
            raise StoreValidationError("direct remote must have a non-empty username")
        if not remote.password_enc:
            raise StoreValidationError("direct remote must have a non-empty password_enc")
    else:  # passthrough
        if not remote.url:
            raise StoreValidationError("passthrough remote must have a non-empty url")
        if remote.username:
            raise StoreValidationError("passthrough remote must have an empty username")
        if remote.password_enc:
            raise StoreValidationError("passthrough remote must have an empty password_enc")


# ---------------------------------------------------------------------------
# ConfigStore
# ---------------------------------------------------------------------------


class ConfigStore:
    """SQLite-backed configuration store.

    Use as a context manager::

        with ConfigStore("/path/to/store.db") as store:
            store.create_config("myconfig")

    Parameters
    ----------
    path:
        Filesystem path to the SQLite database file.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: sqlite3.Connection = sqlite3.connect(self._path, isolation_level=None)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    # -- context manager (convenience; connection is open from __init__) -----

    def __enter__(self) -> ConfigStore:
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: Exception | None,
        exc_tb: object,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise StoreError("ConfigStore has been closed")
        return self._conn

    # -- schema init ---------------------------------------------------------

    def _init_schema(self) -> None:
        conn = self._get_conn()
        cur = conn.cursor()
        cur.executescript(_DDL)

        # Check existing version (if any row exists).
        cur.execute("SELECT version FROM schema_version LIMIT 1")
        row = cur.fetchone()
        if row is not None:
            on_disk_version: int = row[0]
            if on_disk_version > SCHEMA_VERSION:
                raise StoreSchemaError(
                    f"Database schema version {on_disk_version} is newer than "
                    f"supported version {SCHEMA_VERSION}"
                )
            # version == SCHEMA_VERSION → nothing to do
        else:
            # Fresh database – insert version.
            cur.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

    # -- read API ------------------------------------------------------------

    def get_user(self, username: str) -> UserRecord:
        """Return the user record or raise ``StoreNotFoundError``."""
        conn = self._get_conn()
        _validate_username(username)
        row = conn.execute(
            "SELECT username, key_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            raise StoreNotFoundError(f"user not found: {username!r}")
        configs = [
            r[0]
            for r in conn.execute(
                "SELECT config_name FROM user_configs WHERE username = ? ORDER BY rowid",
                (username,),
            ).fetchall()
        ]
        return UserRecord(username=row[0], key_hash=row[1], config_names=tuple(configs))

    def list_users(self) -> tuple[UserRecord, ...]:
        """Return all user records."""
        conn = self._get_conn()
        rows = conn.execute("SELECT username, key_hash FROM users ORDER BY rowid").fetchall()
        result: list[UserRecord] = []
        for r in rows:
            configs = [
                cr[0]
                for cr in conn.execute(
                    "SELECT config_name FROM user_configs WHERE username = ? ORDER BY rowid",
                    (r[0],),
                ).fetchall()
            ]
            result.append(UserRecord(username=r[0], key_hash=r[1], config_names=tuple(configs)))
        return tuple(result)

    def load_config(self, name: str) -> ConfigSnapshot:
        """Return an aggregate snapshot of a config with its remotes and calendars.

        Raises ``StoreNotFoundError`` if the config does not exist.
        """
        conn = self._get_conn()
        _validate_name(name, "config name")
        row = conn.execute("SELECT name FROM configs WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise StoreNotFoundError(f"config not found: {name!r}")

        remote_rows = conn.execute(
            "SELECT config_name, name, url, auth_mode, username, password_enc "
            "FROM remotes WHERE config_name = ? ORDER BY rowid",
            (name,),
        ).fetchall()
        remotes = tuple(
            RemoteRecord(
                config_name=r[0],
                name=r[1],
                url=r[2],
                auth_mode=r[3],  # type: ignore[arg-type]
                username=r[4],
                password_enc=r[5],
            )
            for r in remote_rows
        )

        cal_rows = conn.execute(
            "SELECT config_name, remote_name, name "
            "FROM calendars WHERE config_name = ? ORDER BY rowid",
            (name,),
        ).fetchall()
        calendars = tuple(
            CalendarRecord(config_name=c[0], remote_name=c[1], name=c[2]) for c in cal_rows
        )

        return ConfigSnapshot(name=name, remotes=remotes, calendars=calendars)

    def list_configs(self) -> tuple[str, ...]:
        """Return all config names in insertion order."""
        conn = self._get_conn()
        rows = conn.execute("SELECT name FROM configs ORDER BY rowid").fetchall()
        return tuple(r[0] for r in rows)

    # -- write API -----------------------------------------------------------

    def create_user(self, username: str, key_hash: str) -> None:
        """Create a new user.  Raises ``StoreConflictError`` on duplicate."""
        _validate_username(username)
        if not key_hash:
            raise StoreValidationError("key_hash must not be empty")
        conn = self._get_conn()
        try:
            conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO users (username, key_hash) VALUES (?, ?)",
                (username, key_hash),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            conn.execute("ROLLBACK")
            raise StoreConflictError(f"user already exists: {username!r}") from exc

    def delete_user(self, username: str) -> None:
        """Delete a user.  Raises ``StoreNotFoundError`` if not found."""
        _validate_username(username)
        conn = self._get_conn()
        conn.execute("BEGIN")
        cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        if cur.rowcount == 0:
            conn.execute("ROLLBACK")
            raise StoreNotFoundError(f"user not found: {username!r}")
        conn.execute("COMMIT")

    def set_user_configs(self, username: str, config_names: list[str]) -> None:
        """Replace the set of configs linked to a user.

        FK references to ``configs`` are enforced by the schema.  Raises
        ``StoreNotFoundError`` if the user does not exist, or
        ``StoreConflictError`` if a config does not exist.
        """
        _validate_username(username)
        conn = self._get_conn()
        # Check user exists.
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is None:
            raise StoreNotFoundError(f"user not found: {username!r}")
        try:
            conn.execute("BEGIN")
            conn.execute("DELETE FROM user_configs WHERE username = ?", (username,))
            for cn in config_names:
                conn.execute(
                    "INSERT INTO user_configs (username, config_name) VALUES (?, ?)",
                    (username, cn),
                )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            conn.execute("ROLLBACK")
            raise StoreConflictError(
                f"config not found or other FK violation for user {username!r}"
            ) from exc

    def create_config(self, name: str) -> None:
        """Create a new (empty) config.  Raises ``StoreConflictError`` on duplicate."""
        _validate_name(name, "config name")
        conn = self._get_conn()
        try:
            conn.execute("BEGIN")
            conn.execute("INSERT INTO configs (name) VALUES (?)", (name,))
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            conn.execute("ROLLBACK")
            raise StoreConflictError(f"config already exists: {name!r}") from exc

    def delete_config(self, name: str) -> None:
        """Delete a config and cascade to remotes/calendars/user_configs."""
        _validate_name(name, "config name")
        conn = self._get_conn()
        conn.execute("BEGIN")
        cur = conn.execute("DELETE FROM configs WHERE name = ?", (name,))
        if cur.rowcount == 0:
            conn.execute("ROLLBACK")
            raise StoreNotFoundError(f"config not found: {name!r}")
        conn.execute("COMMIT")

    def create_remote(
        self,
        config_name: str,
        remote: RemoteRecord,
    ) -> None:
        """Create a remote inside a config.

        Validates name rules, direct-auth completeness, and the passthrough
        constraint.  Raises ``StoreConflictError`` on duplicate, FK
        violation, or ``StoreValidationError`` on constraint violations.
        """
        _validate_name(config_name, "config name")
        _validate_name(remote.name, "remote name")
        _validate_remote_auth(remote)
        if remote.config_name != config_name:
            raise StoreValidationError("remote.config_name does not match config_name argument")
        conn = self._get_conn()
        try:
            conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO remotes (config_name, name, url, auth_mode, username, password_enc) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    remote.config_name,
                    remote.name,
                    remote.url,
                    remote.auth_mode,
                    remote.username,
                    remote.password_enc,
                ),
            )
            if remote.auth_mode == "passthrough":
                self._assert_single_passthrough_per_user(conn)
            conn.execute("COMMIT")
        except StoreValidationError:
            conn.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            conn.execute("ROLLBACK")
            raise StoreConflictError(
                f"remote already exists or config {config_name!r} not found"
            ) from exc

    def update_remote(
        self,
        config_name: str,
        name: str,
        remote: RemoteRecord,
    ) -> None:
        """Replace a remote's fields.  Validates name rules and constraints."""
        _validate_name(config_name, "config name")
        _validate_name(name, "remote name")
        _validate_remote_auth(remote)
        if remote.config_name != config_name:
            raise StoreValidationError("remote.config_name does not match config_name argument")
        if remote.name != name:
            raise StoreValidationError("remote.name does not match name argument")
        conn = self._get_conn()
        conn.execute("BEGIN")
        cur = conn.execute(
            "UPDATE remotes SET url = ?, auth_mode = ?, username = ?, password_enc = ? "
            "WHERE config_name = ? AND name = ?",
            (remote.url, remote.auth_mode, remote.username, remote.password_enc, config_name, name),
        )
        if cur.rowcount == 0:
            conn.execute("ROLLBACK")
            raise StoreNotFoundError(f"remote not found: config {config_name!r}, remote {name!r}")
        if remote.auth_mode == "passthrough":
            try:
                self._assert_single_passthrough_per_user(conn)
            except StoreValidationError:
                conn.execute("ROLLBACK")
                raise
        conn.execute("COMMIT")

    def delete_remote(self, config_name: str, name: str) -> None:
        """Delete a remote and cascade to its calendars."""
        _validate_name(config_name, "config name")
        _validate_name(name, "remote name")
        conn = self._get_conn()
        conn.execute("BEGIN")
        cur = conn.execute(
            "DELETE FROM remotes WHERE config_name = ? AND name = ?",
            (config_name, name),
        )
        if cur.rowcount == 0:
            conn.execute("ROLLBACK")
            raise StoreNotFoundError(f"remote not found: config {config_name!r}, remote {name!r}")
        conn.execute("COMMIT")

    def create_calendar(
        self,
        config_name: str,
        remote_name: str,
        name: str,
    ) -> None:
        """Create a calendar inside a remote.  Raises on duplicate or missing parent."""
        _validate_name(config_name, "config name")
        _validate_name(remote_name, "remote name")
        _validate_name(name, "calendar name")
        conn = self._get_conn()
        try:
            conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO calendars (config_name, remote_name, name) VALUES (?, ?, ?)",
                (config_name, remote_name, name),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            conn.execute("ROLLBACK")
            raise StoreConflictError(
                f"calendar already exists or parent remote "
                f"(config={config_name!r}, remote={remote_name!r}) not found"
            ) from exc

    def delete_calendar(
        self,
        config_name: str,
        remote_name: str,
        name: str,
    ) -> None:
        """Delete a calendar."""
        _validate_name(config_name, "config name")
        _validate_name(remote_name, "remote name")
        _validate_name(name, "calendar name")
        conn = self._get_conn()
        conn.execute("BEGIN")
        cur = conn.execute(
            "DELETE FROM calendars WHERE config_name = ? AND remote_name = ? AND name = ?",
            (config_name, remote_name, name),
        )
        if cur.rowcount == 0:
            conn.execute("ROLLBACK")
            raise StoreNotFoundError(
                f"calendar not found: config={config_name!r}, "
                f"remote={remote_name!r}, calendar={name!r}"
            )
        conn.execute("COMMIT")

    # -- internal helpers ----------------------------------------------------

    def _assert_single_passthrough_per_user(self, conn: sqlite3.Connection) -> None:
        """Post-write assertion: every user reaches at most one passthrough remote.

        Called inside an open transaction.  Raises ``StoreValidationError``
        (after the caller rolls back) if the constraint is violated.
        """
        rows = conn.execute(
            "SELECT u.username, COUNT(*) AS pt_count "
            "FROM users u "
            "JOIN user_configs uc ON uc.username = u.username "
            "JOIN remotes r ON r.config_name = uc.config_name "
            "WHERE r.auth_mode = 'passthrough' "
            "GROUP BY u.username "
            "HAVING pt_count > 1"
        ).fetchall()
        if rows:
            offenders = ", ".join(f"{r[0]!r} ({r[1]} passthrough remotes)" for r in rows)
            raise StoreValidationError(
                f"user(s) may have at most one passthrough remote: {offenders}"
            )
