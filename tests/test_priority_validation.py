"""Unit tests for the ``_validate_priority`` helper.

This module (from plan 11a) verifies that ``_validate_priority`` extracts and
validates an iCalendar priority value exactly as the inline logic in
``caldav_create_event`` did: empty values are skipped, non-integers and out-of-range
(0..9) values are rejected, and valid values are returned as an int. All tests are
network-free and deterministic.
"""

import unittest

import server


class PriorityValidationTest(unittest.TestCase):
    """Behavior of ``_validate_priority``."""

    def test_empty_value_is_skipped(self):
        value, err = server._validate_priority("")
        self.assertIsNone(value)
        self.assertIsNone(err)

    def test_valid_lower_bound(self):
        value, err = server._validate_priority("0")
        self.assertEqual(value, 0)
        self.assertIsNone(err)

    def test_valid_upper_bound(self):
        value, err = server._validate_priority("9")
        self.assertEqual(value, 9)
        self.assertIsNone(err)

    def test_valid_mid_value(self):
        value, err = server._validate_priority("5")
        self.assertEqual(value, 5)
        self.assertIsNone(err)

    def test_non_numeric_is_rejected(self):
        value, err = server._validate_priority("high")
        self.assertIsNone(value)
        self.assertEqual(err, "priority must be an integer")

    def test_out_of_range_low_is_rejected(self):
        value, err = server._validate_priority("-1")
        self.assertIsNone(value)
        self.assertEqual(err, "priority must be between 0 and 9")

    def test_out_of_range_high_is_rejected(self):
        value, err = server._validate_priority("10")
        self.assertIsNone(value)
        self.assertEqual(err, "priority must be between 0 and 9")


if __name__ == "__main__":
    unittest.main()
