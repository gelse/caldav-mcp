"""Date/time parsing and formatting helpers.

These helpers read the server timezone and the current-time function through the
:mod:`server` namespace so that tests which patch ``server.SERVER_TZ`` and
``server._now`` observe the patched values.
"""

from datetime import UTC, datetime

import server


def _now():
    """Return the current time in the server timezone."""
    return datetime.now(server.SERVER_TZ)


def _start_of_day(dt):
    """Return the local midnight (start of day) for the given datetime in the server timezone."""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_dt(value):
    value = value.strip()
    if not value:
        return server._now()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
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
                dt = dt.replace(tzinfo=server.SERVER_TZ)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Could not parse datetime: {value!r}")


def _format_ical_dt(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
