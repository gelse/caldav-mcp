"""Simple-mode regression tests (env-only and header mode through the singleton).

Covers the M2.2 requirement: credentials and remote identity flow through
the read-only config singleton.  Each test configures the singleton, patches
the CalDAV boundary (cache / DAVClient), and verifies end-to-end behaviour.

Patch strategy:
* Decorator-level tests: patch ``caldav_mcp.tools._resolve_credentials`` or
  ``caldav_mcp.tools.DAVClient`` / ``caldav_mcp.tools.get_cache``.
* Singleton-level tests: call ``configure_app_config`` / ``reset_app_config``
  directly and verify identity semantics.
* Env is always patched explicitly with ``mock.patch.dict``.
"""

from unittest import mock

import pytest

from caldav_mcp.app_config import (
    AppConfig,
    Config,
    Remote,
    configure_app_config,
    get_app_config,
    reset_app_config,
)
from caldav_mcp.auth import _resolve_credentials
from caldav_mcp.client_cache import ClientCache
from caldav_mcp.errors import AuthError, Status

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENV_REMOTE = Remote(
    name="default",
    url="https://cal.example",
    auth_mode="direct",
    username="alice",
    password="secret",
)

_HEADER_REMOTE = Remote(name="default", url="", auth_mode="passthrough")

_ENV_CONFIG = AppConfig(
    mode="env",
    config=Config(name="default", remotes=(_ENV_REMOTE,), calendars=()),
)

_HEADER_CONFIG = AppConfig(mode="header", config=None)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the config singleton before and after every test."""
    reset_app_config()
    yield
    reset_app_config()


# ---------------------------------------------------------------------------
# 1. env-only mode end-to-end (decorator level)
# ---------------------------------------------------------------------------


def test_env_only_e2e():
    """Env mode: DAVClient receives env creds; cache hit on second call."""
    configure_app_config(_ENV_CONFIG)

    real_cache = ClientCache(max_size=4, ttl_seconds=3600)
    dav_client = mock.MagicMock()

    with (
        mock.patch("caldav_mcp.tools.get_cache", return_value=real_cache),
        mock.patch("caldav_mcp.tools.DAVClient", return_value=dav_client) as mock_dav,
    ):
        # Warm the singleton so the lazy import inside the decorator resolves.
        get_app_config()
        from caldav_mcp.tools import caldav_list_calendars  # noqa: E402

        # First call → cache miss → DAVClient constructed.
        caldav_list_calendars()
        mock_dav.assert_called_once()
        call_kw = mock_dav.call_args
        assert call_kw.kwargs["url"] == "https://cal.example"
        assert call_kw.kwargs["username"] == "alice"
        assert call_kw.kwargs["password"] == "secret"

        # Second call → cache hit → no new DAVClient construction.
        mock_dav.reset_mock()
        with mock.patch("caldav_mcp.tools._get_calendar", return_value=mock.MagicMock()):
            caldav_list_calendars()
        mock_dav.assert_not_called()  # cached client reused


# ---------------------------------------------------------------------------
# 2. header mode end-to-end (decorator level)
# ---------------------------------------------------------------------------


def test_header_mode_e2e():
    """Header mode: _hdrs returns three headers → DAVClient receives them."""
    configure_app_config(_HEADER_CONFIG)

    real_cache = ClientCache(max_size=4, ttl_seconds=3600)
    dav_client = mock.MagicMock()
    hdrs_dict = {
        "x-caldav-url": "https://hdr.example",
        "x-caldav-username": "hdr-user",
        "x-caldav-password": "hdr-pass",
    }

    with (
        mock.patch("caldav_mcp.tools.get_cache", return_value=real_cache),
        mock.patch("caldav_mcp.tools.DAVClient", return_value=dav_client) as mock_dav,
        mock.patch("caldav_mcp.tools._get_calendar", return_value=mock.MagicMock()),
        mock.patch("caldav_mcp.auth._hdrs", return_value=lambda: hdrs_dict),
    ):
        from caldav_mcp.tools import caldav_list_calendars  # noqa: E402

        caldav_list_calendars()

    mock_dav.assert_called_once()
    call_kw = mock_dav.call_args
    assert call_kw.kwargs["url"] == "https://hdr.example"
    assert call_kw.kwargs["username"] == "hdr-user"
    assert call_kw.kwargs["password"] == "hdr-pass"


# ---------------------------------------------------------------------------
# 3. header mode, missing headers
# ---------------------------------------------------------------------------


def test_header_mode_missing_headers():
    """Header mode, headers absent → classified auth failure (Status.AUTH)."""
    configure_app_config(_HEADER_CONFIG)

    with mock.patch("caldav_mcp.auth._hdrs", return_value=lambda: {}):
        from caldav_mcp.tools import caldav_list_calendars  # noqa: E402

        result = caldav_list_calendars()

    assert result.status == Status.AUTH


# ---------------------------------------------------------------------------
# 4. env mode ignores headers
# ---------------------------------------------------------------------------


def test_env_mode_ignores_headers():
    """Env mode + different headers → DAVClient gets env/config values."""
    configure_app_config(_ENV_CONFIG)

    real_cache = ClientCache(max_size=4, ttl_seconds=3600)
    dav_client = mock.MagicMock()
    hdrs_dict = {
        "x-caldav-url": "https://WRONG.example",
        "x-caldav-username": "WRONG",
        "x-caldav-password": "WRONG",
    }

    with (
        mock.patch("caldav_mcp.tools.get_cache", return_value=real_cache),
        mock.patch("caldav_mcp.tools.DAVClient", return_value=dav_client) as mock_dav,
        mock.patch("caldav_mcp.tools._get_calendar", return_value=mock.MagicMock()),
        mock.patch("caldav_mcp.auth._hdrs", return_value=lambda: hdrs_dict),
    ):
        from caldav_mcp.tools import caldav_list_calendars  # noqa: E402

        caldav_list_calendars()

    mock_dav.assert_called_once()
    call_kw = mock_dav.call_args
    assert call_kw.kwargs["url"] == "https://cal.example"
    assert call_kw.kwargs["username"] == "alice"
    assert call_kw.kwargs["password"] == "secret"


# ---------------------------------------------------------------------------
# 5. env mode, direct remote without credentials
# ---------------------------------------------------------------------------


def test_env_mode_no_credentials():
    """Env mode, empty creds → AuthError; no DAVClient, nothing cached."""
    no_creds_remote = Remote(
        name="default",
        url="https://cal.example",
        auth_mode="direct",
        username="",
        password="",
    )
    configure_app_config(
        AppConfig(
            mode="env",
            config=Config(name="default", remotes=(no_creds_remote,), calendars=()),
        )
    )

    real_cache = ClientCache(max_size=4, ttl_seconds=3600)

    with (
        mock.patch("caldav_mcp.tools.get_cache", return_value=real_cache),
        mock.patch("caldav_mcp.tools.DAVClient") as mock_dav,
        mock.patch("caldav_mcp.tools._get_calendar", return_value=mock.MagicMock()),
    ):
        from caldav_mcp.tools import caldav_list_calendars  # noqa: E402

        result = caldav_list_calendars()

    assert result.status == Status.AUTH
    mock_dav.assert_not_called()  # no DAVClient constructed
    assert len(real_cache) == 0  # nothing added to cache


# ---------------------------------------------------------------------------
# 6. singleton read-only under concurrency of calls
# ---------------------------------------------------------------------------


def test_singleton_read_only():
    """Repeated calls never mutate the AppConfig (identity check)."""
    configure_app_config(_ENV_CONFIG)
    app_before = get_app_config()

    with (
        mock.patch("caldav_mcp.tools.get_cache", return_value=ClientCache()),
        mock.patch("caldav_mcp.tools.DAVClient", return_value=mock.MagicMock()),
        mock.patch("caldav_mcp.tools._get_calendar", return_value=mock.MagicMock()),
    ):
        from caldav_mcp.tools import caldav_list_calendars  # noqa: E402

        for _ in range(5):
            caldav_list_calendars()

    app_after = get_app_config()
    assert app_before is app_after  # same object, never mutated


# ---------------------------------------------------------------------------
# 7. _resolve_credentials unit tests through the singleton
# ---------------------------------------------------------------------------


def test_resolve_credentials_direct_with_creds():
    """Direct remote with credentials → returns (url, username, password)."""
    configure_app_config(_ENV_CONFIG)

    url, username, password = _resolve_credentials()
    assert url == "https://cal.example"
    assert username == "alice"
    assert password == "secret"


def test_resolve_credentials_direct_missing_creds():
    """Direct remote missing credentials → AuthError mentioning CALDAV_USERNAME."""
    no_creds_remote = Remote(
        name="default",
        url="https://cal.example",
        auth_mode="direct",
        username="",
        password="",
    )
    configure_app_config(
        AppConfig(
            mode="env",
            config=Config(name="default", remotes=(no_creds_remote,), calendars=()),
        )
    )

    with pytest.raises(AuthError, match="CALDAV_USERNAME"):
        _resolve_credentials()


def test_resolve_credentials_passthrough_with_headers():
    """Passthrough remote with all three headers → header triple."""
    configure_app_config(_HEADER_CONFIG)

    hdrs_dict = {
        "x-caldav-url": "https://hdr.example",
        "x-caldav-username": "hdr-user",
        "x-caldav-password": "hdr-pass",
    }
    with mock.patch("caldav_mcp.auth._hdrs", return_value=lambda: hdrs_dict):
        url, username, password = _resolve_credentials()
    assert url == "https://hdr.example"
    assert username == "hdr-user"
    assert password == "hdr-pass"


def test_resolve_credentials_passthrough_missing_one_header():
    """Passthrough remote missing one header → AuthError (no env mixing)."""
    configure_app_config(_HEADER_CONFIG)

    partial = {"x-caldav-url": "https://hdr.example"}
    with mock.patch("caldav_mcp.auth._hdrs", return_value=lambda: partial):
        with pytest.raises(AuthError, match="Missing CalDAV credentials"):
            _resolve_credentials()
