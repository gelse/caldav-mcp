"""Unit tests for the ``_validate_priority`` helper.

This module (from plan 11a) verifies that ``_validate_priority`` extracts and
validates an iCalendar priority value exactly as the inline logic in
``caldav_create_event`` did: empty values are skipped, non-integers and out-of-range
(0..9) values are rejected, and valid values are returned as an int. All tests are
network-free and deterministic.
"""

import server


def test_empty_value_is_skipped():
    value, err = server._validate_priority("")
    assert value is None
    assert err is None


def test_valid_lower_bound():
    value, err = server._validate_priority("0")
    assert value == 0
    assert err is None


def test_valid_upper_bound():
    value, err = server._validate_priority("9")
    assert value == 9
    assert err is None


def test_valid_mid_value():
    value, err = server._validate_priority("5")
    assert value == 5
    assert err is None


def test_non_numeric_is_rejected():
    value, err = server._validate_priority("high")
    assert value is None
    assert err == "priority must be an integer"


def test_out_of_range_low_is_rejected():
    value, err = server._validate_priority("-1")
    assert value is None
    assert err == "priority must be between 0 and 9"


def test_out_of_range_high_is_rejected():
    value, err = server._validate_priority("10")
    assert value is None
    assert err == "priority must be between 0 and 9"
