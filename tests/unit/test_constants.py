"""Verify that constants are defined and have expected values."""

from caldav_mcp.constants import (
    DEFAULT_ATTENDEE_ROLE,
    ICAL_VERSION,
    MAILTO_PREFIX,
    UID_DOMAIN,
)


def test_default_attendee_role():
    assert DEFAULT_ATTENDEE_ROLE == "REQ-PARTICIPANT"


def test_mailto_prefix():
    assert MAILTO_PREFIX == "mailto:"


def test_uid_domain():
    assert UID_DOMAIN == "caldav-mcp"


def test_ical_version():
    assert ICAL_VERSION == "2.0"
