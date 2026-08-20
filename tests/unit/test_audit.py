"""Unit tests for caldav_mcp.audit — structured audit logging."""

import json
from unittest import mock

from caldav_mcp.audit import log_auth_attempt, log_error, log_operation

# ---------------------------------------------------------------------------
# log_auth_attempt
# ---------------------------------------------------------------------------


@mock.patch("caldav_mcp.audit._audit_log")
def test_log_auth_attempt_success(mock_log):
    """Successful auth emits info-level JSON with event=auth, success=True."""
    log_auth_attempt(success=True, client_ip="10.0.0.1", method="bearer")
    mock_log.info.assert_called_once()
    payload = json.loads(mock_log.info.call_args[0][0])
    assert payload["event"] == "auth"
    assert payload["success"] is True
    assert payload["client_ip"] == "10.0.0.1"
    assert payload["method"] == "bearer"
    assert "ts" in payload


@mock.patch("caldav_mcp.audit._audit_log")
def test_log_auth_attempt_failure(mock_log):
    """Failed auth emits warning-level JSON with success=False and reason."""
    log_auth_attempt(
        success=False,
        client_ip="192.168.1.100",
        method="api-key",
        reason="invalid token",
    )
    mock_log.warning.assert_called_once()
    payload = json.loads(mock_log.warning.call_args[0][0])
    assert payload["event"] == "auth"
    assert payload["success"] is False
    assert payload["client_ip"] == "192.168.1.100"
    assert payload["method"] == "api-key"
    assert payload["reason"] == "invalid token"


@mock.patch("caldav_mcp.audit._audit_log")
def test_log_auth_attempt_default_client_ip(mock_log):
    """client_ip defaults to 'unknown' when not provided."""
    log_auth_attempt(success=False, method="none")
    payload = json.loads(mock_log.warning.call_args[0][0])
    assert payload["client_ip"] == "unknown"


# ---------------------------------------------------------------------------
# log_operation
# ---------------------------------------------------------------------------


@mock.patch("caldav_mcp.audit._audit_log")
def test_log_operation_records_timing(mock_log):
    """log_operation includes duration_ms rounded to 2 decimal places."""
    log_operation("caldav_get_events", "ok", 123.456, calendar_name="Personal")
    mock_log.info.assert_called_once()
    payload = json.loads(mock_log.info.call_args[0][0])
    assert payload["event"] == "tool"
    assert payload["tool"] == "caldav_get_events"
    assert payload["duration_ms"] == 123.46  # rounded
    assert payload["status"] == "ok"
    assert payload["calendar"] == "Personal"


@mock.patch("caldav_mcp.audit._audit_log")
def test_log_operation_records_status(mock_log):
    """log_operation includes the status field."""
    log_operation("caldav_create_event", "error", 5.0)
    payload = json.loads(mock_log.info.call_args[0][0])
    assert payload["status"] == "error"


@mock.patch("caldav_mcp.audit._audit_log")
def test_log_operation_includes_detail(mock_log):
    """Optional detail field is included when provided."""
    log_operation("caldav_get_event_by_uid", "ok", 10.0, detail="evt-123")
    payload = json.loads(mock_log.info.call_args[0][0])
    assert payload["detail"] == "evt-123"


@mock.patch("caldav_mcp.audit._audit_log")
def test_log_operation_has_timestamp(mock_log):
    """log_operation includes a ts (timestamp) field."""
    log_operation("caldav_list_calendars", "ok", 1.0)
    payload = json.loads(mock_log.info.call_args[0][0])
    assert isinstance(payload["ts"], float)
    assert payload["ts"] > 0


# ---------------------------------------------------------------------------
# log_error
# ---------------------------------------------------------------------------


@mock.patch("caldav_mcp.audit._audit_log")
def test_log_error_records_type(mock_log):
    """log_error includes error_type and emits at warning level."""
    log_error("caldav_create_event", "ValueError", "bad input")
    mock_log.warning.assert_called_once()
    payload = json.loads(mock_log.warning.call_args[0][0])
    assert payload["event"] == "error"
    assert payload["tool"] == "caldav_create_event"
    assert payload["error_type"] == "ValueError"
    assert payload["context"] == "bad input"
    assert "ts" in payload


# ---------------------------------------------------------------------------
# No credential leakage
# ---------------------------------------------------------------------------


@mock.patch("caldav_mcp.audit._audit_log")
def test_auth_attempt_never_logs_token(mock_log):
    """Auth audit entries must never contain the actual token/password."""
    log_auth_attempt(
        success=False,
        client_ip="10.0.0.1",
        method="bearer",
        reason="invalid token",
    )
    payload = json.loads(mock_log.warning.call_args[0][0])
    # Ensure no 'token', 'password', 'secret', or 'Authorization' fields
    for key in payload:
        assert "token" not in key.lower()
        assert "password" not in key.lower()
        assert "secret" not in key.lower()
        assert "authorization" not in key.lower()
