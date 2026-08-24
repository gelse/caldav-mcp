"""Unit tests for the ``_validate_rrule`` helper.

This module (from plan 11b) verifies that ``_validate_rrule`` validates an
iCalendar recurrence rule exactly as the inline logic in ``caldav_create_event``
did: empty values are valid, valid RRULE strings are accepted, and malformed
values (including ones that raise or parse to an empty recur without a
frequency) are rejected. All tests are network-free and deterministic.
"""

from caldav_mcp.calendar import _validate_rrule


def test_empty_value_is_valid():
    assert _validate_rrule("")


def test_valid_frequency_is_accepted():
    assert _validate_rrule("FREQ=DAILY")


def test_valid_full_rrule_is_accepted():
    assert _validate_rrule("FREQ=WEEKLY;INTERVAL=2;COUNT=5")


def test_invalid_frequency_is_rejected():
    assert not _validate_rrule("FREQ=BOGUS")


def test_malformed_rrule_is_rejected():
    # e.g. "garbage" parses to an empty vRecur (no frequency) and is rejected.
    assert not _validate_rrule("garbage")


def test_garbage_text_is_rejected():
    assert not _validate_rrule("not-an-rrule")
