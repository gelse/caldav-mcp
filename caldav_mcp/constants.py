"""Shared constants for the caldav-mcp package.

Centralizes magic strings, default values, and protocol-level identifiers
so they are defined once and referenced by name throughout the codebase.
"""

from __future__ import annotations

# ── iCalendar protocol constants ────────────────────────────────────────

PRODID = "-//caldav-mcp//EN"
ICAL_VERSION = "2.0"
UID_DOMAIN = "caldav-mcp"

# ── Attendee defaults (RFC 5545) ───────────────────────────────────────

DEFAULT_ATTENDEE_ROLE = "REQ-PARTICIPANT"
DEFAULT_PARTSTAT = "NEEDS-ACTION"
DEFAULT_RSVP = "TRUE"

# ── Mailto normalization ────────────────────────────────────────────────

MAILTO_PREFIX = "mailto:"

# ── Error messages ─────────────────────────────────────────────────────

ERR_NO_COMPONENT = "no icalendar component"
ERR_INVALID_RRULE = "invalid RRULE"
