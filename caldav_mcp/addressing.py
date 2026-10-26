"""Dotted-path addressing and pro-mode access filtering.

In pro (DB) mode, every calendar is uniquely identified by a *dotted path*
``config.remote.calendar`` — three non-empty, dot-separated segments.
This eliminates any ambiguity when multiple configs or remotes are loaded:
write tools are never confused about which calendar to target.

**Why dotted paths exist** (``ideas/db-config-enhancement.md``, lines 43–48):
In simple (env/header) mode a single implicit remote is in scope, so a plain
calendar name suffices.  In pro mode multiple named configs and remotes can
coexist; the dotted path is the single unambiguous addressing primitive.

**No-dots store guarantee**: M3's charset regex
(``^[A-Za-z0-9][A-Za-z0-9_-]*$``) ensures config, remote, and calendar
names never contain a dot, so the first ``.`` in a well-formed path is
always a separator.

**No-enumeration policy**: Access-denied and not-found both return the same
generic ``ValueError("unknown calendar path '…'")``.  Callers cannot probe
which configs or users exist — consistent with M4.2's auth no-enumeration
decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from caldav_mcp.app_config import (
    AppConfig,
    Remote,
    find_calendar,
    find_config,
    find_remote,
)

if TYPE_CHECKING:
    from caldav_mcp.db_loader import ProUser


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalendarAddress:
    """Parsed representation of a ``config.remote.calendar`` dotted path."""

    config_name: str
    remote_name: str
    calendar_name: str


@dataclass(frozen=True)
class Resolution:
    """Resolved remote and calendar name for a dotted-path address."""

    remote: Remote
    calendar_name: str  # live server-side name (== address.calendar_name)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_GENERIC_ERROR_TEMPLATE = "unknown calendar path '{path}'"


def parse_dotted_path(path: str) -> CalendarAddress:
    """Split *path* on ``"."`` and return a :class:`CalendarAddress`.

    The path must contain **exactly 3 non-empty, non-whitespace parts**
    separated by dots (``config.remote.calendar``).  The M3 no-dots charset
    rule guarantees real store names never contain a dot, so this structural
    split is the only gate — we do **not** apply the store charset regex here;
    validation against the loaded config is the real check.

    Raises
    ------
    ValueError
        When the path does not contain exactly 3 non-empty, non-whitespace
        parts.  The message names the expected form ``config.remote.calendar``.
    """
    parts = path.split(".")
    if len(parts) != 3:
        raise ValueError(
            f"Expected calendar path in 'config.remote.calendar' format, "
            f"got {len(parts)} segment(s): '{path}'"
        )
    for i, part in enumerate(parts):
        if not part.strip():
            raise ValueError(
                f"Expected calendar path in 'config.remote.calendar' format, "
                f"got empty or whitespace-only segment at position {i + 1}: '{path}'"
            )
    return CalendarAddress(
        config_name=parts[0],
        remote_name=parts[1],
        calendar_name=parts[2],
    )


# ---------------------------------------------------------------------------
# Resolution with access filtering
# ---------------------------------------------------------------------------


def resolve_addressed_calendar(
    app: AppConfig,
    user: ProUser | None,
    path: str,
) -> Resolution:
    """Resolve a dotted path to a :class:`Resolution` (remote + calendar name).

    The access check runs **after** parsing but **before** any existence
    lookups, so callers cannot enumerate which configs or remotes exist by
    observing different error messages for access-denied vs not-found.

    Parameters
    ----------
    app : AppConfig
        The application config singleton (must be in ``"db"`` mode with
        populated ``configs``).
    user : ProUser or None
        The authenticated user (from :func:`~caldav_mcp.auth._authenticate`).
        When ``None`` (simple-mode test callers / legacy paths) access
        filtering is skipped entirely.
    path : str
        A ``config.remote.calendar`` dotted path.

    Returns
    -------
    Resolution
        The resolved remote and calendar name.

    Raises
    ------
    ValueError
        For malformed paths (via :func:`parse_dotted_path`), access denial,
        or unknown config/remote/calendar — all with the same generic message
        to prevent enumeration.
    """
    addr = parse_dotted_path(path)

    # ── Access check (after parse, before existence lookups) ───────────
    if user is not None:
        if addr.config_name not in user.config_names:
            raise ValueError(_GENERIC_ERROR_TEMPLATE.format(path=path))

    # ── Config lookup ──────────────────────────────────────────────────
    cfg = find_config(app, addr.config_name)
    if cfg is None:
        raise ValueError(_GENERIC_ERROR_TEMPLATE.format(path=path))

    # ── Remote lookup ──────────────────────────────────────────────────
    remote = find_remote(cfg, addr.remote_name)
    if remote is None:
        raise ValueError(_GENERIC_ERROR_TEMPLATE.format(path=path))

    # ── Calendar lookup ────────────────────────────────────────────────
    cal = find_calendar(cfg, addr.remote_name, addr.calendar_name)
    if cal is None:
        raise ValueError(_GENERIC_ERROR_TEMPLATE.format(path=path))

    return Resolution(remote=remote, calendar_name=cal.name)
