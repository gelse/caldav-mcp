"""Unit tests for the shared API-token auth guard in server.py.

These tests verify _const_eq() and _require_auth(), plus the fact that
guarded tools short-circuit to the unauthorized error before resolving
CalDAV credentials. Auth headers are simulated by patching
server.get_http_headers() to return a dict of lowercase header keys.
"""

import unittest
from unittest import mock

import server

UNAUTHORIZED = "ERROR: unauthorized - missing or invalid API token"


class ConstEqTest(unittest.TestCase):
    def test_equal_strings_returns_true(self):
        self.assertTrue(server._const_eq("secret-token", "secret-token"))

    def test_differing_strings_returns_false(self):
        self.assertFalse(server._const_eq("secret-token", "secret-tokez"))

    def test_differing_length_returns_false(self):
        self.assertFalse(server._const_eq("secret", "secret-token"))

    def test_differing_length_reversed_returns_false(self):
        self.assertFalse(server._const_eq("secret-token", "secret"))

    def test_empty_string_equals_empty_string(self):
        self.assertTrue(server._const_eq("", ""))


class RequireAuthTest(unittest.TestCase):
    def test_disabled_auth_passes(self):
        with mock.patch.object(server, "API_KEY", ""):
            result = server._require_auth()
        self.assertEqual(result, "")

    def test_valid_authorization_bearer_passes(self):
        with mock.patch.object(server, "API_KEY", "secret-token"):
            with mock.patch.object(
                server,
                "get_http_headers",
                return_value={"authorization": "Bearer secret-token"},
            ):
                result = server._require_auth()
        self.assertEqual(result, "")

    def test_valid_x_api_key_passes(self):
        with mock.patch.object(server, "API_KEY", "secret-token"):
            with mock.patch.object(
                server,
                "get_http_headers",
                return_value={"x-api-key": "secret-token"},
            ):
                result = server._require_auth()
        self.assertEqual(result, "")

    def test_missing_token_fails(self):
        with mock.patch.object(server, "API_KEY", "secret-token"):
            with mock.patch.object(server, "get_http_headers", return_value={}):
                result = server._require_auth()
        self.assertEqual(result, UNAUTHORIZED)

    def test_wrong_token_fails(self):
        with mock.patch.object(server, "API_KEY", "secret-token"):
            with mock.patch.object(
                server,
                "get_http_headers",
                return_value={"authorization": "Bearer wrong"},
            ):
                result = server._require_auth()
        self.assertEqual(result, UNAUTHORIZED)

    def test_bearer_scheme_case_insensitive_passes(self):
        with mock.patch.object(server, "API_KEY", "secret-token"):
            with mock.patch.object(
                server,
                "get_http_headers",
                return_value={"authorization": "bearer secret-token"},
            ):
                result = server._require_auth()
        self.assertEqual(result, "")

    def test_malformed_authorization_falls_back_to_api_key(self):
        with mock.patch.object(server, "API_KEY", "secret-token"):
            with mock.patch.object(
                server,
                "get_http_headers",
                return_value={
                    "authorization": "Basic abc123",
                    "x-api-key": "secret-token",
                },
            ):
                result = server._require_auth()
        self.assertEqual(result, "")


class GuardedToolRejectsUnauthenticatedTest(unittest.TestCase):
    def test_guarded_tool_short_circuits_before_credentials(self):
        with mock.patch.object(server, "API_KEY", "secret-token"):
            with mock.patch.object(server, "get_http_headers", return_value={}):
                with mock.patch.object(
                    server,
                    "_resolve_credentials",
                    side_effect=AssertionError("credentials must not be resolved"),
                ):
                    result = server.caldav_list_calendars()
        self.assertEqual(result, UNAUTHORIZED)


if __name__ == "__main__":
    unittest.main()
