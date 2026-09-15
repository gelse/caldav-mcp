"""Unit tests for the shared API-token auth guard in caldav_mcp.auth.

These tests verify _const_eq() and _require_auth(), plus the fact that
guarded tools short-circuit to the unauthorized result before resolving
CalDAV credentials. Auth headers are simulated by patching
caldav_mcp.auth._hdrs to return a dict of lowercase header keys.
"""

import os
from unittest import mock

import pytest

import caldav_mcp.config as config
import server
from caldav_mcp import auth
from caldav_mcp.errors import AuthError
from server import Status


def test_equal_strings_returns_true():
    assert server._const_eq("secret-token", "secret-token")


def test_differing_strings_returns_false():
    assert not server._const_eq("secret-token", "secret-tokez")


def test_differing_length_returns_false():
    assert not server._const_eq("secret", "secret-token")


def test_differing_length_reversed_returns_false():
    assert not server._const_eq("secret-token", "secret")


def test_empty_string_equals_empty_string():
    assert server._const_eq("", "")


def test_disabled_auth_passes():
    with mock.patch.object(config, "API_KEY", ""):
        result = auth._require_auth()
    assert result is None


def test_valid_authorization_bearer_passes():
    with mock.patch.object(config, "API_KEY", "secret-token"):
        with mock.patch.object(
            auth,
            "_hdrs",
            return_value=lambda: {"authorization": "Bearer secret-token"},
        ):
            result = auth._require_auth()
    assert result is None


def test_valid_x_api_key_passes():
    with mock.patch.object(config, "API_KEY", "secret-token"):
        with mock.patch.object(
            auth,
            "_hdrs",
            return_value=lambda: {"x-api-key": "secret-token"},
        ):
            result = auth._require_auth()
    assert result is None


def test_missing_token_fails():
    with mock.patch.object(config, "API_KEY", "secret-token"):
        with mock.patch.object(auth, "_hdrs", return_value=lambda: {}):
            result = auth._require_auth()
    assert result.status == Status.AUTH


def test_wrong_token_fails():
    with mock.patch.object(config, "API_KEY", "secret-token"):
        with mock.patch.object(
            auth,
            "_hdrs",
            return_value=lambda: {"authorization": "Bearer wrong"},
        ):
            result = auth._require_auth()
    assert result.status == Status.AUTH


def test_bearer_scheme_case_insensitive_passes():
    with mock.patch.object(config, "API_KEY", "secret-token"):
        with mock.patch.object(
            auth,
            "_hdrs",
            return_value=lambda: {"authorization": "bearer secret-token"},
        ):
            result = auth._require_auth()
    assert result is None


def test_malformed_authorization_falls_back_to_api_key():
    with mock.patch.object(config, "API_KEY", "secret-token"):
        with mock.patch.object(
            auth,
            "_hdrs",
            return_value=lambda: {
                "authorization": "Basic abc123",
                "x-api-key": "secret-token",
            },
        ):
            result = auth._require_auth()
    assert result is None


def test_guarded_tool_short_circuits_before_credentials():
    with mock.patch.object(config, "API_KEY", "secret-token"):
        with mock.patch.object(auth, "_hdrs", return_value=lambda: {}):
            with mock.patch.object(
                auth,
                "_resolve_credentials",
                side_effect=AssertionError("credentials must not be resolved"),
            ):
                result = server.caldav_list_calendars()
    assert result.status == Status.AUTH


# ---------------------------------------------------------------------------
# Precedence resolution tests (Step M1.1 — mode-based credential resolution)
# ---------------------------------------------------------------------------

_ENV_TRIPLE = {
    "CALDAV_URL": "https://env.example.com/caldav",
    "CALDAV_USERNAME": "env-user",
    "CALDAV_PASSWORD": "env-pass",
}

_HDR_TRIPLE = {
    "x-caldav-url": "https://header.example.com/caldav",
    "x-caldav-username": "header-user",
    "x-caldav-password": "header-pass",
}


def test_env_mode_headers_ignored():
    """Env mode: env triple set + different headers → returns env triple."""
    with mock.patch.dict(os.environ, _ENV_TRIPLE):
        with mock.patch.object(auth, "_hdrs", return_value=lambda: _HDR_TRIPLE):
            url, username, password = auth._resolve_credentials()
    assert url == "https://env.example.com/caldav"
    assert username == "env-user"
    assert password == "env-pass"


def test_env_mode_garbage_headers_ignored():
    """Env mode: env triple set + nonsense headers → returns env triple."""
    garbage = {"x-caldav-url": "not-a-real-url", "x-caldav-username": "x", "x-caldav-password": "y"}
    with mock.patch.dict(os.environ, _ENV_TRIPLE):
        with mock.patch.object(auth, "_hdrs", return_value=lambda: garbage):
            url, username, password = auth._resolve_credentials()
    assert url == "https://env.example.com/caldav"
    assert username == "env-user"
    assert password == "env-pass"


def test_env_mode_missing_env_username_password():
    """Env mode: CALDAV_URL set, CALDAV_USERNAME/CALDAV_PASSWORD unset → raises AuthError."""
    env = {"CALDAV_URL": "https://env.example.com/caldav"}
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch.object(auth, "_hdrs", return_value=lambda: _HDR_TRIPLE):
            with pytest.raises(AuthError, match="CALDAV_USERNAME"):
                auth._resolve_credentials()


def test_env_mode_whitespace_url_counts_as_unset():
    """Env mode: CALDAV_URL=' ' with valid headers → header mode applies."""
    env = {"CALDAV_URL": " "}
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch.object(auth, "_hdrs", return_value=lambda: _HDR_TRIPLE):
            url, username, password = auth._resolve_credentials()
    assert url == "https://header.example.com/caldav"
    assert username == "header-user"
    assert password == "header-pass"


def test_header_mode_all_headers_present():
    """Header mode: CALDAV_* env vars absent + three headers → returns header triple."""
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch.object(auth, "_hdrs", return_value=lambda: _HDR_TRIPLE):
            url, username, password = auth._resolve_credentials()
    assert url == "https://header.example.com/caldav"
    assert username == "header-user"
    assert password == "header-pass"


def test_header_mode_no_headers_no_env():
    """Header mode: no headers, no env → raises AuthError."""
    with mock.patch.dict(os.environ, {}, clear=True):
        with mock.patch.object(auth, "_hdrs", return_value=lambda: {}):
            with pytest.raises(AuthError):
                auth._resolve_credentials()


def test_header_mode_partial_headers_no_mixing():
    """Header mode: partial X-Caldav-Url + env CALDAV_USERNAME/PASSWORD → AuthError."""
    partial = {"x-caldav-url": "https://header.example.com/caldav"}
    env = {"CALDAV_USERNAME": "env-user", "CALDAV_PASSWORD": "env-pass"}
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch.object(auth, "_hdrs", return_value=lambda: partial):
            with pytest.raises(AuthError):
                auth._resolve_credentials()


def test_header_mode_header_url_with_env_username():
    """Header mode: full header triple + env username/password → returns header triple."""
    env = {"CALDAV_USERNAME": "env-user", "CALDAV_PASSWORD": "env-pass"}
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch.object(auth, "_hdrs", return_value=lambda: _HDR_TRIPLE):
            url, username, password = auth._resolve_credentials()
    assert url == "https://header.example.com/caldav"
    assert username == "header-user"
    assert password == "header-pass"


# ---------------------------------------------------------------------------
# Regression: F1 — Authorization header must survive get_http_headers() call
# ---------------------------------------------------------------------------


def test_hdrs_includes_authorization():
    """_hdrs() wrapper must call get_http_headers with include={'authorization'}.

    FastMCP strips ``authorization`` by default; the wrapper must explicitly
    request it so that Bearer auth works in both simple and pro mode.
    """
    sentinel = {"authorization": "Bearer real-token"}
    mock_ghh = mock.MagicMock(return_value=sentinel)
    with mock.patch("fastmcp.server.dependencies.get_http_headers", mock_ghh):
        result = auth._hdrs()()
    assert result is sentinel
    mock_ghh.assert_called_once_with(include={"authorization"})


def test_bearer_auth_works_end_to_end():
    """End-to-end: Authorization: Bearer header reaches _extract_key via real _hdrs.

    Verifies the F1 fix: _hdrs() passes include={'authorization'} so the
    Bearer token is not silently dropped before auth can read it.
    """
    with mock.patch.object(config, "API_KEY", "the-secret"):
        mock_ghh = mock.MagicMock(return_value={"authorization": "Bearer the-secret"})
        with mock.patch("fastmcp.server.dependencies.get_http_headers", mock_ghh):
            result = auth._require_auth()
    assert result is None, "Bearer auth must succeed when get_http_headers includes authorization"
