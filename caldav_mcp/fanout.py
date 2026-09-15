"""Read-tool fan-out with per-remote aggregation (M5.1 partial-success).

In pro (DB) mode, read tools execute across **all calendars of all remotes of
all configs** the authenticated user may access, and aggregate results per
remote into one :class:`~caldav_mcp.errors.ToolResult`.

Per-remote status model
-----------------------

Each ``(remote × calendar)`` pair produces one :class:`AggregatedEntry` with
addressing fields (``config_name``, ``remote_name``, ``calendar_name``) plus
the handler's ``data`` payload, an optional ``error`` string, and a per-entry
``status`` string (one of ``"ok"`` | ``"empty"`` | ``"auth"`` | ``"error"`` |
``"not_found"``).  Classification rules:

* ``AuthError`` → ``"auth"``
* ``NotFoundError`` → ``"not_found"``
* Any other exception → ``"error"``
* Query returning no data → ``"empty"``
* Success → ``"ok"``

Top-level status rule (unchanged from M4.4)
--------------------------------------------

* ``Status.OK`` when **at least one** scope succeeds (``"ok"`` or ``"empty"``).
* ``Status.ERROR`` when **all** scopes fail (``"auth"``/``"error"``/``"not_found"``).
* ``Status.EMPTY`` for zero accessible calendars.

Render format
-------------

The message is built by :func:`render_fanout_message` and always starts with
the top-level tag (``OK`` / ``ERROR:[server]``) followed by a summary
(``N ok, M error`` counts — ``"ok"`` and ``"empty"`` count as ok;
``"auth"``/``"error"``/``"not_found"`` count as failures), then one
``- [status] config.remote: detail`` line per remote in declaration order
(grouped by ``(config_name, remote_name)`` when a remote spans several
calendars).

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
from caldav_mcp.errors import AuthError, NotFoundError, Status

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
    status : str
        Per-entry status string (``"ok"`` | ``"empty"`` | ``"auth"`` |
        ``"error"`` | ``"not_found"``).
    """

    config_name: str
    remote_name: str
    calendar_name: str
    data: Any = None
    error: str | None = None
    status: str = "ok"


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

# ---------------------------------------------------------------------------
# Per-scope exception classification
# ---------------------------------------------------------------------------

_VALID_STATUSES = frozenset({"ok", "empty", "auth", "error", "not_found"})

# Severity ordering for per-remote aggregation: higher index = more severe.
# When a remote spans several calendars with differing outcomes, we pick the
# most severe entry status as the remote's representative status.
_SEVERITY: dict[str, int] = {"ok": 0, "empty": 0, "not_found": 1, "auth": 2, "error": 3}


def _classify_exception(exc: Exception) -> str:
    """Map an exception to a per-entry status string.

    ``AuthError`` → ``"auth"``, ``NotFoundError`` → ``"not_found"``,
    anything else → ``"error"``.
    """
    if isinstance(exc, AuthError):
        return "auth"
    if isinstance(exc, NotFoundError):
        return "not_found"
    return "error"


def aggregate_remote_status(entries: list[AggregatedEntry] | tuple[AggregatedEntry, ...]) -> str:
    """Derive a single per-remote status from a group of entries.

    When a remote spans several calendars with differing outcomes (e.g.
    one calendar succeeds and another fails), this function picks one
    representative status for the whole remote.

    **Rule** (deterministic, severity-based):

    * If any entry has a failure status (``"error"``, ``"auth"``,
      ``"not_found"``), return the most severe one using the fixed
      priority ``error`` > ``auth`` > ``"not_found"``.  A remote with
      at least one failure is never reported as ``"ok"``.
    * If all entries are ``"ok"`` or ``"empty"``, return ``"ok"``.  A
      remote with only non-failing entries always reports success.

    Parameters
    ----------
    entries : list or tuple of AggregatedEntry
        The entries belonging to one ``(config_name, remote_name)`` group.

    Returns
    -------
    str
        One of the valid status strings from :data:`_VALID_STATUSES`.
    """
    worst_status = "ok"
    for e in entries:
        if e.status not in _VALID_STATUSES:
            # Defensive: treat unknown statuses as errors.
            return "error"
        if _SEVERITY[e.status] > _SEVERITY[worst_status]:
            worst_status = e.status
    return worst_status


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
            status = _classify_exception(exc)
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
                            status=status,
                        )
                    )
            else:
                entries.append(
                    AggregatedEntry(
                        config_name=scope.config_name,
                        remote_name=scope.remote.name,
                        calendar_name="",
                        error=str(exc),
                        status=status,
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
                status = _classify_exception(exc)
                entries.append(
                    AggregatedEntry(
                        config_name=scope.config_name,
                        remote_name=scope.remote.name,
                        calendar_name=cal_label,
                        error=str(exc),
                        status=status,
                    )
                )

    return _aggregate(entries)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def render_fanout_message(
    entries: tuple[AggregatedEntry, ...],
    top_status: Status,
) -> str:
    """Build the human-readable fan-out message.

    The first line is the top-level tag (``OK`` / ``ERROR:[server]``) plus a
    summary of counts (``N ok, M error``).  ``"ok"`` and ``"empty"`` count as
    ok; ``"auth"``/``"error"``/``"not_found"`` count as failures.

    Then one ``- [status] config.remote: detail`` line per **remote** in
    declaration order.  When a remote spans several calendars, entries are
    grouped into a single line with a count (e.g. ``3 calendars`` or
    ``12 events``).  On failure the detail is the exception text.

    Parameters
    ----------
    entries : tuple[AggregatedEntry, ...]
        Ordered entries from the fan-out executor.
    top_status : Status
        The top-level status (``OK``, ``ERROR``, ``EMPTY``).

    Returns
    -------
    str
        Rendered message string (multi-line).
    """
    if not entries:
        return "No accessible calendars"

    # Count ok vs failure for the summary line.
    ok_count = 0
    fail_count = 0
    for e in entries:
        if e.status in ("ok", "empty"):
            ok_count += 1
        else:
            fail_count += 1

    # First line: tag + summary
    tag = {
        Status.OK: "OK",
        Status.ERROR: "ERROR:[server]",
        Status.EMPTY: "OK",
    }[top_status]

    summary_parts: list[str] = []
    if ok_count:
        summary_parts.append(f"{ok_count} ok")
    if fail_count:
        summary_parts.append(f"{fail_count} error")
    summary = ", ".join(summary_parts) if summary_parts else "0 ok"

    first_line = f"{tag} Fan-out across {len(entries)} scopes: {summary}"

    # Group entries by (config_name, remote_name) preserving declaration order.
    from collections import OrderedDict

    remote_groups: OrderedDict[tuple[str, str], list[AggregatedEntry]] = OrderedDict()
    for e in entries:
        key = (e.config_name, e.remote_name)
        remote_groups.setdefault(key, []).append(e)

    detail_lines: list[str] = []
    for (cfg, rname), group in remote_groups.items():
        # Determine the detail for this remote group.
        if all(g.status in ("ok", "empty") for g in group):
            # Success: show count of calendars or events.
            has_calendar = any(g.calendar_name for g in group)
            if has_calendar:
                detail = f"{len(group)} calendar{'s' if len(group) != 1 else ''}"
            else:
                detail = f"{len(group)} scope{'s' if len(group) != 1 else ''}"
            detail_lines.append(f"- [ok] {cfg}.{rname}: {detail}")
        elif all(g.status not in ("ok", "empty") for g in group):
            # All failed for this remote: show first exception text.
            err_text = next(g.error or "unknown error" for g in group)
            status_tag = group[0].status
            detail_lines.append(f"- [{status_tag}] {cfg}.{rname}: {err_text}")
        else:
            # Mixed outcomes within one remote (e.g. one calendar ok,
            # another error).  Derive the remote's representative status
            # using the shared severity rule.
            remote_status = aggregate_remote_status(group)
            ok_c = sum(1 for g in group if g.status in ("ok", "empty"))
            err_c = len(group) - ok_c
            detail_lines.append(f"- [{remote_status}] {cfg}.{rname}: {ok_c} ok, {err_c} error")

    return first_line + "\n" + "\n".join(detail_lines)


def _aggregate(entries: list[AggregatedEntry]) -> AggregatedResult:
    """Build an :class:`AggregatedResult` from the collected entries."""
    if not entries:
        return AggregatedResult(status=Status.EMPTY, message="No accessible calendars")

    ok_count = sum(1 for e in entries if e.status in ("ok", "empty"))
    err_count = len(entries) - ok_count

    if err_count == len(entries):
        # All scopes failed → Status.ERROR
        top_status = Status.ERROR
    elif ok_count > 0:
        # At least one succeeded → Status.OK
        top_status = Status.OK
    else:
        # All entries present but none ok — defensive fallback.
        # Preserve the M4.4 zero-scope EMPTY message text.
        return AggregatedResult(
            status=Status.EMPTY,
            message="No results",
            entries=tuple(entries),
        )

    message = render_fanout_message(tuple(entries), top_status)
    return AggregatedResult(
        status=top_status,
        message=message,
        entries=tuple(entries),
    )
