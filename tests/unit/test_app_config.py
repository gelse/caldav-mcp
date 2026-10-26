"""Unit tests for caldav_mcp.app_config — config singleton module.

Covers the loader (load_app_config) and the singleton accessors
(get_app_config / configure_app_config / reset_app_config) for both
env and header modes.  All tests explicitly patch os.environ for the
three CALDAV_* keys to avoid leaking dev-machine state.
"""

import dataclasses
import os
from unittest import mock

import pytest

from caldav_mcp.app_config import (
    PASSTHROUGH_REMOTE,
    AppConfig,
    Calendar,
    Config,
    Remote,
    configure_app_config,
    get_app_config,
    implicit_remote,
    load_app_config,
    reset_app_config,
)

# ---------------------------------------------------------------------------
# Fixture: reset singleton on every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Ensure the module-level singleton is always clean."""
    reset_app_config()
    yield
    reset_app_config()


# ---------------------------------------------------------------------------
# Loader tests (cases 1–7)
# ---------------------------------------------------------------------------


def test_env_mode_shape():
    """Case 1: CALDAV_URL/USERNAME/PASSWORD set → env mode with correct fields."""
    env = {
        "CALDAV_URL": "https://cal.example.com",
        "CALDAV_USERNAME": "alice",
        "CALDAV_PASSWORD": "s3cret",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        app = load_app_config()

    assert app.mode == "env"
    assert app.config is not None
    assert app.config.name == "default"
    assert len(app.config.remotes) == 1

    remote = app.config.remotes[0]
    assert remote.name == "default"
    assert remote.url == "https://cal.example.com"
    assert remote.auth_mode == "direct"
    assert remote.username == "alice"
    assert remote.password == "s3cret"


def test_env_mode_strips_url():
    """Case 2: CALDAV_URL with leading/trailing whitespace is stripped."""
    env = {
        "CALDAV_URL": " https://cal.example ",
        "CALDAV_USERNAME": "bob",
        "CALDAV_PASSWORD": "pw",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        app = load_app_config()

    assert app.mode == "env"
    remote = app.config.remotes[0]
    assert remote.url == "https://cal.example"


def test_whitespace_only_url_counts_as_unset():
    """Case 3: CALDAV_URL that is only whitespace → header mode."""
    env = {
        "CALDAV_URL": "   ",
        "CALDAV_USERNAME": "carol",
        "CALDAV_PASSWORD": "pw",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        app = load_app_config()

    assert app.mode == "header"
    assert app.config is None


def test_env_mode_missing_credentials_loads():
    """Case 4: CALDAV_URL set but username/password absent → empty strings."""
    env = {"CALDAV_URL": "https://cal.example.com"}
    with mock.patch.dict(os.environ, env, clear=True):
        app = load_app_config()

    assert app.mode == "env"
    remote = app.config.remotes[0]
    assert remote.username == ""
    assert remote.password == ""


def test_header_mode_shape():
    """Case 5: all three env vars absent → header mode, config is None."""
    env: dict[str, str] = {}
    with mock.patch.dict(os.environ, env, clear=True):
        app = load_app_config()

    assert app.mode == "header"
    assert app.config is None


def test_header_mode_with_stray_credentials():
    """Case 6: CALDAV_URL absent but username/password set → still header."""
    env = {
        "CALDAV_USERNAME": "dave",
        "CALDAV_PASSWORD": "pw",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        app = load_app_config()

    assert app.mode == "header"
    assert app.config is None


def test_dataclasses_are_frozen():
    """Case 7: Calendar, Remote, Config, AppConfig are immutable."""
    cal = Calendar(name="team")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cal.name = "other"  # type: ignore[misc]

    remote = Remote(name="r", url="https://x", auth_mode="direct")
    with pytest.raises(dataclasses.FrozenInstanceError):
        remote.name = "other"  # type: ignore[misc]

    config = Config(name="c", remotes=(), calendars=())
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.name = "other"  # type: ignore[misc]

    app = AppConfig(mode="env", config=config)
    with pytest.raises(dataclasses.FrozenInstanceError):
        app.mode = "header"  # type: ignore[misc]

    # Verify collections are tuples
    assert isinstance(config.remotes, tuple)
    assert isinstance(config.calendars, tuple)


# ---------------------------------------------------------------------------
# Singleton tests (cases 8–11)
# ---------------------------------------------------------------------------


def test_singleton_lazy_load():
    """Case 8: reset → get_app_config() loads from env; second call is same object."""
    env = {
        "CALDAV_URL": "https://cal.example.com",
        "CALDAV_USERNAME": "alice",
        "CALDAV_PASSWORD": "pw",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        reset_app_config()
        first = get_app_config()
        second = get_app_config()

    assert first.mode == "env"
    assert first is second  # identity check


def test_singleton_configure_and_reset():
    """Case 9: configure_app_config(custom) → singleton returns custom; reset reloads."""
    custom = AppConfig(mode="header", config=None)
    configure_app_config(custom)
    assert get_app_config() is custom

    reset_app_config()

    env = {
        "CALDAV_URL": "https://cal.example.com",
        "CALDAV_USERNAME": "alice",
        "CALDAV_PASSWORD": "pw",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        reloaded = get_app_config()

    assert reloaded is not custom
    assert reloaded.mode == "env"


def test_singleton_header_mode_passthrough_remote():
    """Case 10: header mode → config is None, implicit_remote is PASSTHROUGH_REMOTE."""
    env: dict[str, str] = {}
    with mock.patch.dict(os.environ, env, clear=True):
        reset_app_config()
        app = get_app_config()

    assert app.mode == "header"
    assert app.config is None

    remote = implicit_remote(app)
    assert remote is PASSTHROUGH_REMOTE
    assert remote.auth_mode == "passthrough"
    assert remote.url == ""


def test_singleton_env_mode_implicit_remote():
    """Case 11: env mode → implicit_remote returns built-in direct remote."""
    env = {
        "CALDAV_URL": "https://cal.example.com",
        "CALDAV_USERNAME": "alice",
        "CALDAV_PASSWORD": "pw",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        reset_app_config()
        app = get_app_config()

    remote = implicit_remote(app)
    assert remote.auth_mode == "direct"
    assert remote.url == "https://cal.example.com"
    assert remote.username == "alice"
    assert remote.password == "pw"
    assert remote.name == "default"
