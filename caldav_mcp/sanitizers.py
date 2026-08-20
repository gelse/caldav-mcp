"""Input sanitization and validation for CalDAV MCP tool parameters."""

import re

# Maximum lengths for string fields (RFC 5545 has no hard limits, but
# reasonable bounds prevent abuse).
MAX_SUMMARY_LENGTH = 256
MAX_LOCATION_LENGTH = 512
MAX_DESCRIPTION_LENGTH = 4096
MAX_CATEGORIES_LENGTH = 1024
MAX_CALENDAR_NAME_LENGTH = 128
MAX_QUERY_LENGTH = 512

# Characters that could cause iCalendar injection or display issues.
# Newlines are allowed in descriptions (multi-line text is valid in iCalendar).
DANGEROUS_PATTERNS = [
    re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]"),  # control chars except \t \n \r
]


def sanitize_text(value: str, max_length: int = 4096) -> str:
    """Strip control characters and enforce a maximum length.

    Raises ``ValueError`` if the cleaned value exceeds *max_length*.
    """
    cleaned = value
    for pattern in DANGEROUS_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > max_length:
        raise ValueError(f"Input exceeds maximum length of {max_length} characters")
    return cleaned


def validate_calendar_name(name: str) -> str:
    """Validate and sanitize a calendar name.

    Calendar names must be non-empty, contain only safe characters, and
    respect the maximum length. Raises ``ValueError`` on invalid input.
    """
    name = name.strip()
    if not name:
        raise ValueError("Calendar name must not be empty")
    # Allow alphanumeric, spaces, hyphens, underscores, dots, slashes, and
    # common Unicode letters. Block characters that could break CalDAV paths.
    if not re.match(r"^[\w\s\-./@+() ]+$", name, re.UNICODE):
        raise ValueError(f"Calendar name contains invalid characters: {name!r}")
    if len(name) > MAX_CALENDAR_NAME_LENGTH:
        raise ValueError(f"Calendar name exceeds {MAX_CALENDAR_NAME_LENGTH} characters")
    return name


def validate_email(email: str) -> str:
    """Validate an email address for use as a CalDAV attendee.

    Raises ``ValueError`` if the email is malformed.
    """
    email = email.strip()
    if not email:
        raise ValueError("Email address must not be empty")
    # RFC 5322 simplified pattern
    if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email):
        raise ValueError(f"Invalid email address: {email!r}")
    return email


def limit_string_length(value: str, max_length: int, field_name: str = "field") -> str:
    """Enforce a maximum length on a string, raising ``ValueError`` if exceeded."""
    if len(value) > max_length:
        raise ValueError(f"{field_name} exceeds maximum length of {max_length} characters")
    return value
