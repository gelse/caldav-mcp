"""Read-only application config singleton for caldav-mcp.

This module provides a lightweight, immutable configuration layer that
mirrors the conceptual data model described in ``ideas/db-config-enhancement.md``
(lines 25–34).  The ``Config`` / ``Remote`` / ``Calendar`` dataclasses are
intentionally shaped so that the M4 database loader can produce the same
structure without any downstream changes.

**Read-only after init.**  Once loaded (or explicitly configured) the
``AppConfig`` instance is frozen — to apply env-var changes, restart the
process.  All dataclasses use ``frozen=True`` and collections are tuples.

Two modes, matching M1 semantics (``plans/M1.1-precedence-resolution.md``):

* **env mode** — ``CALDAV_URL`` is set and non-empty after ``.strip()``.
  Credentials come from the environment (``CALDAV_USERNAME`` / ``CALDAV_PASSWORD``).
  ``X-Caldav-*`` request headers are ignored.

* **header mode** — ``CALDAV_URL`` is unset or whitespace-only after
  ``.strip()``.  CalDAV credentials are expected on every request via
  ``X-Caldav-Url`` / ``X-Caldav-Username`` / ``X-Caldav-Password`` headers.

Singleton accessors (``get_app_config`` / ``configure_app_config`` /
``reset_app_config``) follow the injectable-singleton pattern of
``caldav_mcp.client_cache``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

AuthMode = Literal["direct", "passthrough"]


@dataclass(frozen=True)
class Calendar:
    """A calendar exposed on a remote, addressable by name."""

    name: str


@dataclass(frozen=True)
class Remote:
    """One CalDAV server.

    *auth_mode* is ``"direct"`` when the server carries stored credentials,
    or ``"passthrough"`` when credentials come from each request.
    """

    name: str
    url: str
    auth_mode: AuthMode
    username: str = ""
    password: str = ""  # empty for passthrough remotes


@dataclass(frozen=True)
class Config:
    """A named collection of remotes and per-remote calendars.

    ``calendars`` is a tuple of ``(remote_name, (Calendar, …))`` pairs.
    In simple (env) mode this tuple is typically empty — calendar discovery
    stays live against the CalDAV server.  The per-remote lists preserve the
    ``config.remote.calendar`` shape the M4 DB loader will produce.
    """

    name: str
    remotes: tuple[Remote, ...]
    calendars: tuple[tuple[str, tuple[Calendar, ...]], ...]


@dataclass(frozen=True)
class AppConfig:
    """Top-level application config produced by :func:`load_app_config`."""

    mode: Literal["env", "header", "db"]
    config: Config | None  # None in header mode and in "db" mode
    configs: tuple[Config, ...] = ()  # populated only in "db" mode


# ---------------------------------------------------------------------------
# Built-in constants
# ---------------------------------------------------------------------------

PASSTHROUGH_REMOTE = Remote(name="default", url="", auth_mode="passthrough")
"""Implicit remote used in header mode — credentials arrive per-request."""

DEFAULT_CALENDARS: tuple[Calendar, ...] = ()
"""In simple mode no calendars are declared statically."""


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_app_config() -> AppConfig:
    """Build an :class:`AppConfig` from the current ``os.environ``.

    Reads ``CALDAV_URL``, ``CALDAV_USERNAME``, and ``CALDAV_PASSWORD`` at call
    time (do **not** import from ``caldav_mcp.config`` which caches at import
    time — that would defeat per-test env patching).

    Only ``CALDAV_URL`` is ``.strip()``-ed before the emptiness check; all
    other values are stored as-is.  Missing ``CALDAV_USERNAME`` /
    ``CALDAV_PASSWORD`` do **not** raise — they default to empty strings.
    """
    raw_url = os.environ.get("CALDAV_URL", "")
    url = raw_url.strip()

    if url:
        return AppConfig(
            mode="env",
            config=Config(
                name="default",
                remotes=(
                    Remote(
                        name="default",
                        url=url,
                        auth_mode="direct",
                        username=os.environ.get("CALDAV_USERNAME", ""),
                        password=os.environ.get("CALDAV_PASSWORD", ""),
                    ),
                ),
                calendars=(),
            ),
        )

    return AppConfig(mode="header", config=None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def implicit_remote(app: AppConfig) -> Remote:
    """Return the built-in remote for the given *app* config.

    In env mode this is the direct remote carrying the env credentials; in
    header mode it is :data:`PASSTHROUGH_REMOTE`.

    Raises ``ValueError`` in "db" mode — db mode has no single implicit
    remote; callers must use :func:`find_remote` instead.
    """
    if app.mode == "db":
        raise ValueError(
            "implicit_remote() is not available in db mode; "
            "use find_remote() to address remotes explicitly"
        )
    if app.mode == "env" and app.config is not None:
        return app.config.remotes[0]
    return PASSTHROUGH_REMOTE


# ---------------------------------------------------------------------------
# Lookup helpers (linear scans — fine for startup-sized data)
# ---------------------------------------------------------------------------


def find_config(app: AppConfig, config_name: str) -> Config | None:
    """Return the named config from *app*, or ``None`` if not found."""
    if app.config is not None and app.config.name == config_name:
        return app.config
    for cfg in app.configs:
        if cfg.name == config_name:
            return cfg
    return None


def find_remote(config: Config, remote_name: str) -> Remote | None:
    """Return the named remote from *config*, or ``None`` if not found."""
    for remote in config.remotes:
        if remote.name == remote_name:
            return remote
    return None


def find_calendar(config: Config, remote_name: str, calendar_name: str) -> Calendar | None:
    """Return the named calendar within *remote_name*, or ``None`` if not found."""
    for rname, calendars in config.calendars:
        if rname == remote_name:
            for cal in calendars:
                if cal.name == calendar_name:
                    return cal
    return None


# ---------------------------------------------------------------------------
# Module-level singleton (injectable accessor)
# ---------------------------------------------------------------------------
# No locking is needed: the event loop is single-threaded, the benign
# first-access race (two concurrent callers both see None and both call
# load_app_config) is harmless because the result is idempotent, and this
# matches the existing client_cache singleton precedent.

_UNSET = object()  # sentinel: no explicit configure_app_config() call yet

_app_config: AppConfig | None = _UNSET  # type: ignore[assignment]
_explicit: bool = False  # True when set via configure_app_config()
_env_cache_key: str | None = None  # tracks env snapshot for cache invalidation


def _env_cache_key_value() -> str:
    """Return a snapshot string of the env vars that determine the config mode."""
    return "|".join(
        (
            os.environ.get("CALDAV_URL", ""),
            os.environ.get("CALDAV_USERNAME", ""),
            os.environ.get("CALDAV_PASSWORD", ""),
        )
    )


def configure_app_config(app: AppConfig) -> None:
    """Install an explicit process-wide config (called once at startup / in tests).

    When set, :func:`get_app_config` returns this value instead of
    re-reading the environment.  Call :func:`reset_app_config` to clear
    the override and restore lazy env loading.
    """
    global _app_config, _explicit, _env_cache_key  # noqa: PLW0603
    _app_config = app
    _explicit = True
    _env_cache_key = None


def get_app_config() -> AppConfig:
    """Return the active config.

    If an explicit config was installed via :func:`configure_app_config`,
    return it.  Otherwise lazily load from ``os.environ``, caching the
    result until the relevant env vars change (so that
    ``mock.patch.dict(os.environ, …)`` in tests takes effect while
    repeated calls within the same env context return the same object).
    """
    global _app_config, _explicit, _env_cache_key  # noqa: PLW0603
    if _explicit and _app_config is not None:
        return _app_config
    key = _env_cache_key_value()
    if _app_config is not None and _env_cache_key == key:
        return _app_config  # cache hit — env unchanged
    _app_config = load_app_config()
    _env_cache_key = key
    return _app_config


def reset_app_config() -> None:
    """Drop any explicit config; next :func:`get_app_config` re-reads env."""
    global _app_config, _explicit, _env_cache_key  # noqa: PLW0603
    _app_config = None
    _explicit = False
    _env_cache_key = None
