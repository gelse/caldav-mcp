"""Date/time parsing and formatting helpers.

Uses a lazy accessor for the server module so that ``SERVER_TZ`` and ``_now``
are resolved at call time, allowing ``mock.patch.object(server, ...)`` to
take effect in tests.
"""

from datetime import UTC, datetime


def _srv():
    """Lazy accessor for the ``server`` module – avoids circular top-level import."""
    import server  # noqa: E402  (deferred; see module docstring)

    return server


def _now() -> datetime:
    """Return the current time in the server timezone."""
    return datetime.now(_srv().SERVER_TZ)


def _start_of_day(dt: datetime) -> datetime:
    """Return the local midnight (start of day) for the given datetime in the server timezone."""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_dt(value: str) -> datetime:
    srv = _srv()
    value = value.strip()
    if not value:
        return srv._now()  # type: ignore[no-any-return]
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    # Try ISO 8601 variants in decreasing specificity.  Timezone-aware formats
    # are tried first; naive results are assumed to be in the server timezone.
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=srv.SERVER_TZ)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Could not parse datetime: {value!r}")


def _format_ical_dt(dt: datetime) -> str:
    """Format a datetime as an iCalendar UTC timestamp (``YYYYMMDDTHHMMSSZ``).

    Naive datetimes are assumed to be in UTC.  The result is always in UTC
    per RFC 5545 §3.3.5.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
