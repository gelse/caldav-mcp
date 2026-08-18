"""Unit tests for the typed error-classification behavior in server.py.

These tests verify that the tools from plan 04a/04b classify errors correctly:
- AuthError   -> "ERROR:[auth] ..."
- NotFoundError -> "ERROR:[not_found] ..."
- unexpected exceptions -> "ERROR:[server] Internal error" via _log_exception
and that _log_exception never leaks raw exception/credential material.

These tests are network-free: CalDAV boundaries are stubbed with
mock.patch.object on _resolve_credentials, _client, and _get_calendar.
"""

import unittest
from unittest import mock

import server


class AuthErrorClassificationTest(unittest.TestCase):
    def test_missing_credentials_returns_auth_error(self):
        with mock.patch.object(
            server,
            "_resolve_credentials",
            side_effect=server.AuthError("missing credentials"),
        ):
            result = server.caldav_list_calendars()
        self.assertTrue(result.startswith("ERROR:[auth]"))
        self.assertIn("missing credentials", result)


class NotFoundClassificationTest(unittest.TestCase):
    def test_missing_calendar_returns_not_found(self):
        with mock.patch.object(
            server, "_resolve_credentials", return_value=("u", "p", "w")
        ), mock.patch.object(server, "_client", return_value=object()), mock.patch.object(
            server,
            "_get_calendar",
            side_effect=server.NotFoundError("Calendar 'x' not found"),
        ):
            result = server.caldav_get_events()
        self.assertTrue(result.startswith("ERROR:[not_found]"))
        self.assertIn("Calendar 'x' not found", result)


class ServerErrorClassificationTest(unittest.TestCase):
    def test_unexpected_exception_logged_and_sanitized(self):
        with mock.patch.object(
            server, "_resolve_credentials", side_effect=RuntimeError("boom")
        ), mock.patch.object(
            server,
            "_log_exception",
            return_value="ERROR:[server] Internal error",
        ) as m:
            result = server.caldav_list_calendars()
        self.assertTrue(result.startswith("ERROR:[server]"))
        self.assertEqual(result, "ERROR:[server] Internal error")
        m.assert_called_once()

    def test_unexpected_exception_does_not_leak_raw_message(self):
        secret = "hunter2-supersecret-password"
        with mock.patch.object(
            server,
            "_resolve_credentials",
            side_effect=RuntimeError("connection failed with password=%s" % secret),
        ), mock.patch.object(
            server,
            "_log_exception",
            return_value="ERROR:[server] Internal error",
        ):
            result = server.caldav_list_calendars()
        self.assertTrue(result.startswith("ERROR:[server]"))
        self.assertNotIn(secret, result)
        self.assertNotIn("connection failed", result)


class LogExceptionTest(unittest.TestCase):
    def test_log_exception_returns_sanitized_message(self):
        with mock.patch.object(server.log, "exception") as log_exc:
            result = server._log_exception(RuntimeError("boom"), "caldav_foo")
        self.assertEqual(result, "ERROR:[server] Internal error")
        log_exc.assert_called_once_with("Unhandled error in %s", "caldav_foo")

    def test_log_exception_never_leaks_exception_text(self):
        secret = "s3cret-value"
        exc = RuntimeError("priviledge escalation with key=%s" % secret)
        with mock.patch.object(server.log, "exception"):
            result = server._log_exception(exc, "caldav_foo")
        self.assertNotIn(secret, result)
        self.assertNotIn("priviledge", result)


if __name__ == "__main__":
    unittest.main()
