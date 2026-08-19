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

from unittest import mock

import pytest
from caldav.lib.error import DAVError

import server
from server import Status


def test_missing_credentials_returns_auth_error():
    with mock.patch.object(
        server,
        "_resolve_credentials",
        side_effect=server.AuthError("missing credentials"),
    ):
        result = server.caldav_list_calendars()
    assert result.status == Status.AUTH
    assert "missing credentials" in result.message


def test_missing_calendar_returns_not_found():
    with (
        mock.patch.object(server, "_resolve_credentials", return_value=("u", "p", "w")),
        mock.patch.object(server, "DAVClient", return_value=object()),
        mock.patch.object(
            server,
            "_get_calendar",
            side_effect=server.NotFoundError("Calendar 'x' not found"),
        ),
    ):
        result = server.caldav_get_events()
    assert result.status == Status.NOT_FOUND
    assert "Calendar 'x' not found" in result.message


def test_unexpected_exception_propagates():
    """Unexpected exceptions are no longer swallowed by the handler."""
    with mock.patch.object(server, "_resolve_credentials", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            server.caldav_list_calendars()


def test_dav_error_is_caught_and_logged():
    """Expected remote (DAVError) failures are still caught and rendered."""
    with (
        mock.patch.object(
            server,
            "_resolve_credentials",
            side_effect=DAVError(url="https://caldav.example", reason="boom"),
        ),
        mock.patch.object(server.log, "exception") as log_exc,
    ):
        result = server.caldav_list_calendars()
    assert result.status == Status.ERROR
    assert result.message == "Internal error"
    log_exc.assert_called_once_with("Unhandled error in %s", "caldav_list_calendars")


def test_dav_error_does_not_leak_raw_message():
    secret = "hunter2-supersecret-password"
    with mock.patch.object(
        server,
        "_resolve_credentials",
        side_effect=DAVError(url=f"https://caldav.example/{secret}", reason="boom"),
    ):
        result = server.caldav_list_calendars()
    assert result.status == Status.ERROR
    assert secret not in result.message
    assert "connection failed" not in result.message


def test_log_exception_returns_sanitized_message():
    with mock.patch.object(server.log, "exception") as log_exc:
        result = server._log_exception(RuntimeError("boom"), "caldav_foo")
    assert result.status == Status.ERROR
    assert result.message == "Internal error"
    log_exc.assert_called_once_with("Unhandled error in %s", "caldav_foo")


def test_log_exception_never_leaks_exception_text():
    secret = "s3cret-value"
    exc = RuntimeError(f"priviledge escalation with key={secret}")
    with mock.patch.object(server.log, "exception"):
        result = server._log_exception(exc, "caldav_foo")
    assert secret not in result.message
    assert "priviledge" not in result.message


def test_log_exception_verbose_context_logged():
    """The exact exception text is logged server-side, not returned."""
    secret = "top-secret"
    exc = RuntimeError(f"detail is logged here {secret}")
    with mock.patch.object(server.log, "exception") as log_exc:
        result = server._log_exception(exc, "caldav_foo")
    log_exc.assert_called_once_with("Unhandled error in %s", "caldav_foo")
    assert secret not in result.message
    assert "detail is logged here" not in result.message
