"""Read-tool fan-out with per-remote aggregation (M4.4).

In pro (DB) mode, read tools execute across **all calendars of all remotes of
all configs** the authenticated user may access, and aggregate results per
remote into one :class:`~caldav_mcp.errors.ToolResult`.

Aggregation shape (M4 contract — M5 may extend, not break)
------------------------------------------------------------

Each ``(remote × calendar)`` pair produces one :class:`AggregatedEntry` with
addressing fields (``config_name``, ``remote_name``, ``calendar_name``) plus
the handler's ``data`` payload and an optional ``error`` string.  The final
:class:`~caldav_mcp.errors.ToolResult` is:

* ``Status.OK`` with ``data`` = list of entries and a per-remote summary
  ``message`` when **at least one** scope succeeds; failed entries carry
  ``error="<exception text>"``.
* ``Status.ERROR`` naming each failing remote when **all** scopes error.
* ``Status.EMPTY`` for zero accessible calendars or all-empty results.

M5 forward-compatibility promise
---------------------------------

The ``AggregatedEntry`` shape and ``Status.OK/ERROR/EMPTY`` semantics are
the stable contract.  M5 will add per-remote independent statuses, richer
partial-failure reporting, and parallel execution — but will not break the
entry fields or the three-way status rule established here.

Sequential-execution decision (do not deviate)
-----------------------------------------------

Remotes execute **sequentially** in config/remote declaration order.  Rationale:

1. The shared :mod:`caldav_mcp.client_cache` and
   :func:`~caldav_mcp.calendar._get_calendar` are not designed for concurrent
   first-contact (``DAVClient`` principal discovery is the expensive path and
   cache writes race).
2. MCP tool calls are already user-interactive, and pro deployments start
   small (1–3 remotes).
3. M5's per-remote failure reporting wants deterministic ordering.

Parallelism can be revisited after M5 without changing result shapes (the
executor is isolated in one function).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from caldav_mcp.app_config import AppConfig, Remote
from caldav_mcp.errors import Status

if __name__ != "caldav_mcp.fanout":
    # TYPE_CHECKING guard to avoid circular import at runtime
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        pass


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RemoteScope:
    """One unit of fan-out work: a remote and its calendars within a config.

    Attributes
    ----------
    config_name : str
        The config this scope belongs to.
    remote : Remote
        The remote to connect to.
    calendar_names : tuple[str, ...]
        Calendar names to query on this remote (may be empty for
        ``caldav_list_calendars``-style fan-outs).
    """

    config_name: str
    remote: Remote
    calendar_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class AggregatedEntry:
    """One result row in the fan-out aggregation.

    Attributes
    ----------
    config_name : str
        Config the entry came from.
    remote_name : str
        Remote name (human-readable identifier).
    calendar_name : str
        Calendar name (empty string for ``caldav_list_calendars``).
    data : Any
        The handler's result payload (dict, list, etc.).
    error : str | None
        Exception text when this scope failed; ``None`` on success.
    """

    config_name: str
    remote_name: str
    calendar_name: str
    data: Any = None
    error: str | None = None


@dataclass(frozen=True)
class AggregatedResult:
    """Final result of a fan-out execution.

    Attributes
    ----------
    status : Status
        ``OK`` when at least one scope succeeded; ``ERROR`` when all errored;
        ``EMPTY`` for zero entries.
    message : str
        Per-remote summary lines (human-readable).
    entries : tuple[AggregatedEntry, ...]
        Ordered list of per-scope entries.
    """

    status: Status
    message: str = ""
    entries: tuple[AggregatedEntry, ...] = ()


# ---------------------------------------------------------------------------
# Scope computation
# ---------------------------------------------------------------------------


def accessible_scopes(
    app: AppConfig,
    user: Any,  # ProUser | None — Any to avoid circular import at runtime
) -> tuple[RemoteScope, ...]:
    """Compute the list of remote scopes accessible to *user*.

    Intersects ``app.configs`` with ``user.config_names`` and produces one
    :class:`RemoteScope` per ``(config, remote, calendars)`` triple,
    preserving store declaration order (``Config.remotes`` / ``calendars``
    tuples).

    Parameters
    ----------
    app : AppConfig
        The application config singleton (must be in ``"db"`` mode).
    user : ProUser
        The authenticated user.  Must not be ``None`` in pro mode.

    Returns
    -------
    tuple[RemoteScope, ...]
        Ordered scopes for the fan-out executor.
    """
    if user is None:
        return ()

    scopes: list[RemoteScope] = []
    for cfg in app.configs:
        if cfg.name not in user.config_names:
            continue
        for remote in cfg.remotes:
            # Gather calendar names for this remote from the config's
            # calendar groups.  Preserve declaration order.
            cal_names: list[str] = []
            for rname, calendars in cfg.calendars:
                if rname == remote.name:
                    cal_names.extend(c.name for c in calendars)
            scopes.append(
                RemoteScope(
                    config_name=cfg.name,
                    remote=remote,
                    calendar_names=tuple(cal_names),
                )
            )
    return tuple(scopes)


# ---------------------------------------------------------------------------
# Fan-out executor
# ---------------------------------------------------------------------------

# All exceptions that the fan-out query function might raise and that should
# be captured as per-entry errors rather than propagated.
_FANOUT_ERRORS = (Exception,)


def run_fanout(
    scopes: tuple[RemoteScope, ...],
    credential_headers: dict[str, str],
    query_fn: Callable[[RemoteScope, Any], AggregatedEntry | None],
) -> AggregatedResult:
    """Execute *query_fn* per ``(remote × calendar)`` sequentially.

    Parameters
    ----------
    scopes : tuple[RemoteScope, ...]
        The accessible scopes (from :func:`accessible_scopes`).
    credential_headers : dict[str, str]
        Captured ``X-Caldav-*`` request headers for passthrough remotes.
    query_fn : callable
        ``(scope: RemoteScope, cal: Any) -> AggregatedEntry | None``.
        Called once per ``(scope, calendar)`` pair.  ``cal`` is the live
        calendar object (from ``client.principal().calendars()``) or ``None``
        for ``needs_calendar=False`` tools (e.g. ``caldav_list_calendars``).
        May raise; exceptions are captured into the entry's ``error`` field.

    Returns
    -------
    AggregatedResult
        Aggregated result with per-remote summary and ordered entries.
    """
    from caldav_mcp.tools import _resolve_pro_client_for_scope

    entries: list[AggregatedEntry] = []

    for scope in scopes:
        try:
            _resolve_pro_client_for_scope(scope.remote, credential_headers)
        except Exception as exc:
            # Cannot connect to this remote at all — create error entries
            # for each calendar in the scope.
            if scope.calendar_names:
                for cal_name in scope.calendar_names:
                    entries.append(
                        AggregatedEntry(
                            config_name=scope.config_name,
                            remote_name=scope.remote.name,
                            calendar_name=cal_name,
                            error=str(exc),
                        )
                    )
            else:
                entries.append(
                    AggregatedEntry(
                        config_name=scope.config_name,
                        remote_name=scope.remote.name,
                        calendar_name="",
                        error=str(exc),
                    )
                )
            continue

        # Determine which calendars to iterate over.
        # For needs_calendar=False tools (caldav_list_calendars),
        # calendar_names is empty → run once with cal=None.
        calendars_to_query: list[str | None]
        if scope.calendar_names:
            calendars_to_query = list(scope.calendar_names)
        else:
            calendars_to_query = [None]

        for cal_name in calendars_to_query:  # type: ignore[assignment]
            cal_label = cal_name or ""
            try:
                entry = query_fn(scope, cal_name)
                if entry is not None:
                    entries.append(entry)
            except Exception as exc:
                entries.append(
                    AggregatedEntry(
                        config_name=scope.config_name,
                        remote_name=scope.remote.name,
                        calendar_name=cal_label,
                        error=str(exc),
                    )
                )

    return _aggregate(entries)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _aggregate(entries: list[AggregatedEntry]) -> AggregatedResult:
    """Build an :class:`AggregatedResult` from the collected entries."""
    if not entries:
        return AggregatedResult(status=Status.EMPTY, message="No accessible calendars")

    ok_count = sum(1 for e in entries if e.error is None)
    err_count = len(entries) - ok_count

    # Build per-remote summary lines.
    remote_stats: dict[str, tuple[int, int]] = {}
    for e in entries:
        rname = e.remote_name
        ok, err = remote_stats.get(rname, (0, 0))
        if e.error is None:
            remote_stats[rname] = (ok + 1, err)
        else:
            remote_stats[rname] = (ok, err + 1)

    summary_parts: list[str] = []
    for rname, (r_ok, r_err) in remote_stats.items():
        if r_err == 0:
            summary_parts.append(f"{rname}: {r_ok} ok")
        elif r_ok == 0:
            summary_parts.append(f"{rname}: {r_err} failed")
        else:
            summary_parts.append(f"{rname}: {r_ok} ok, {r_err} failed")
    message = "; ".join(summary_parts)

    if err_count == len(entries):
        # All scopes failed → Status.ERROR
        failing_remotes = sorted({e.remote_name for e in entries})
        message = "All remotes failed: " + ", ".join(failing_remotes)
        return AggregatedResult(
            status=Status.ERROR,
            message=message,
            entries=tuple(entries),
        )

    if ok_count > 0:
        # At least one succeeded → Status.OK
        return AggregatedResult(
            status=Status.OK,
            message=message,
            entries=tuple(entries),
        )

    # All entries present but all have error (shouldn't happen given the
    # err_count == len(entries) check above, but be defensive).
    return AggregatedResult(status=Status.EMPTY, message="No results")
