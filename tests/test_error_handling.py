"""Unit tests for the typed error-classification behavior in server.py.

These tests verify that the tools classify errors via the structured
``ToolResult`` returned by :func:`server._render_error`:

- AuthError   -> ``status == Status.AUTH``
- NotFoundError -> ``status == Status.NOT_FOUND``
- unexpected exceptions -> ``status == Status.ERROR`` and a sanitized message
- and that logging/rendering never leaks raw exception/credential material.

These tests are network-free: CalDAV boundaries are stubbed with
mock.patch.object on _resolve_credentials, _client, and _get_calendar.
"""

import unittest
from unittest import mock

import server
from server import Status


class AuthErrorClassificationTest(unittest.TestCase):
    def test_missing_credentials_returns_auth_error(self):
        with mock.patch.object(
            server,
            "_resolve_credentials",
            side_effect=server.AuthError("missing credentials"),
        ):
            result = server.caldav_list_calendars()
        self.assertEqual(result.status, Status.AUTH)
        self.assertIn("missing credentials", result.message)


class NotFoundClassificationTest(unittest.TestCase):
    def test_missing_calendar_returns_not_found(self):
        with mock.patch.object(
            server, "_resolve_credentials", return_value=("u", "p", "w")
        ), mock.patch.object(server, "DAVClient", return_value=object()), mock.patch.object(
            server,
            "_get_calendar",
            side_effect=server.NotFoundError("Calendar 'x' not found"),
        ):
            result = server.caldav_get_events()
        self.assertEqual(result.status, Status.NOT_FOUND)
        self.assertIn("Calendar 'x' not found", result.message)


class ServerErrorClassificationTest(unittest.TestCase):
    def test_unexpected_exception_logged_and_sanitized(self):
        with mock.patch.object(
            server, "_resolve_credentials", side_effect=RuntimeError("boom")
        ), mock.patch.object(server.log, "exception") as log_exc:
            result = server.caldav_list_calendars()
        self.assertEqual(result.status, Status.ERROR)
        self.assertEqual(result.message, "Internal error")
        log_exc.assert_called_once_with("Unhandled error in %s", "caldav_list_calendars")

    def test_unexpected_exception_does_not_leak_raw_message(self):
        secret = "hunter2-supersecret-password"
        with mock.patch.object(
            server,
            "_resolve_credentials",
            side_effect=RuntimeError(f"connection failed with password={secret}"),
        ):
            result = server.caldav_list_calendars()
        self.assertEqual(result.status, Status.ERROR)
        self.assertNotIn(secret, result.message)
        self.assertNotIn("connection failed", result.message)


class LogExceptionTest(unittest.TestCase):
    def test_log_exception_returns_sanitized_message(self):
        with mock.patch.object(server.log, "exception") as log_exc:
            result = server._log_exception(RuntimeError("boom"), "caldav_foo")
        self.assertEqual(result.status, Status.ERROR)
        self.assertEqual(result.message, "Internal error")
        log_exc.assert_called_once_with("Unhandled error in %s", "caldav_foo")

    def test_log_exception_never_leaks_exception_text(self):
        secret = "s3cret-value"
        exc = RuntimeError(f"priviledge escalation with key={secret}")
        with mock.patch.object(server.log, "exception"):
            result = server._log_exception(exc, "caldav_foo")
        self.assertNotIn(secret, result.message)
        self.assertNotIn("priviledge", result.message)

    def test_log_exception_verbose_context_logged(self):
        """The exact exception text is logged server-side, not returned."""
        secret = "top-secret"
        exc = RuntimeError(f"detail is logged here {secret}")
        with mock.patch.object(server.log, "exception") as log_exc:
            result = server._log_exception(exc, "caldav_foo")
        log_exc.assert_called_once_with("Unhandled error in %s", "caldav_foo")
        self.assertNotIn(secret, result.message)
        self.assertNotIn("detail is logged here", result.message)


if __name__ == "__main__":
    unittest.main()
