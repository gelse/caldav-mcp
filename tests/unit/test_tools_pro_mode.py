"""Unit tests for pro-mode tool behaviour (M4.3 — dotted-path addressing).

Patch discipline (following M4.2 / M2.2 idioms):

* ``_authenticate`` patched at ``caldav_mcp.tools._authenticate`` to inject
  the pro user and skip real endpoint auth.
* ``_is_pro_mode`` patched at ``caldav_mcp.auth._is_pro_mode`` for the
  auth-layer pro mode gate.
* ``configure_app_config`` / ``reset_app_config`` for the db-mode config.
* ``DAVClient`` patched at ``caldav_mcp.tools.DAVClient`` and the cache
  cleared per test.

Test numbering follows the plan §Test requirements 7–14.
"""

from __future__ import annotations

from unittest import mock

import pytest

import caldav_mcp.tools as tools_mod
from caldav_mcp.app_config import (
    AppConfig,
    Calendar,
    Config,
    Remote,
    configure_app_config,
    reset_app_config,
)
from caldav_mcp.db_loader import ProUser
from caldav_mcp.errors import Status
from caldav_mcp.tools import get_cache

# ---------------------------------------------------------------------------
# Fabricated db-mode AppConfig
# ---------------------------------------------------------------------------

_REMOTE_NC = Remote(
    name="nc",
    url="https://cal.example/dav",
    auth_mode="direct",
    username="alice",
    password="secret",
)
_REMOTE_NC2 = Remote(
    name="nc2",
    url="https://cal2.example/dav",
    auth_mode="direct",
    username="alice2",
    password="secret2",
)
_REMOTE_PT = Remote(
    name="passthrough",
    url="",
    auth_mode="passthrough",
)

_CONFIG_WORK = Config(
    name="work",
    remotes=(_REMOTE_NC, _REMOTE_NC2),
    calendars=(
        ("nc", (Calendar(name="team"), Calendar(name="personal"))),
        ("nc2", (Calendar(name="shared"),)),
    ),
)

_CONFIG_PERSONAL = Config(
    name="personal",
    remotes=(_REMOTE_PT,),
    calendars=(("passthrough", (Calendar(name="mycal"),)),),
)

_DB_APP = AppConfig(mode="db", config=None, configs=(_CONFIG_WORK, _CONFIG_PERSONAL))

_USER_WORK = ProUser(username="alice", key_hash="h", config_names=("work",))
_USER_BOTH = ProUser(username="both", key_hash="h2", config_names=("work", "personal"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _pro_mode_setup():
    """Install db-mode config and clean up per test."""
    configure_app_config(_DB_APP)
    get_cache().clear()
    yield
    reset_app_config()
    get_cache().clear()


def _fake_cal(name="team", url="https://cal.example/team"):
    """Return a minimal mock calendar."""
    cal = mock.MagicMock()
    cal.name = name
    cal.url = url
    return cal


def _fake_client(cal=None):
    """Return a mock DAVClient with a principal returning *cal*."""
    client = mock.MagicMock()
    principal = mock.MagicMock()
    calendars = [cal] if cal else []
    principal.calendars.return_value = calendars
    client.principal.return_value = principal
    return client


def _auth_return(pro_user):
    """Return the ``(pro_user, error)`` tuple for ``_authenticate`` patches."""
    return (pro_user, None)


# ===================================================================
# 7. Write tool, no dots → ERROR, no DAVClient
# ===================================================================


class TestWriteToolNoDots:
    """Test requirement 7: plain calendar name in pro mode → ERROR."""

    def test_create_event_no_dots_error(self):
        """caldav_create_event with calendar_name='team' (no dots) → ERROR."""
        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient") as mock_dav,
        ):
            result = tools_mod.caldav_create_event(
                summary="Test",
                start="2026-01-15T10:00",
                calendar_name="team",
            )
        assert result.status == Status.ERROR
        assert "config.remote.calendar" in result.message.lower()
        mock_dav.assert_not_called()

    def test_update_event_no_dots_error(self):
        """caldav_update_event with plain name → ERROR."""
        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient") as mock_dav,
        ):
            result = tools_mod.caldav_update_event(uid="x", calendar_name="team")
        assert result.status == Status.ERROR
        mock_dav.assert_not_called()

    def test_delete_event_no_dots_error(self):
        """caldav_delete_event with plain name → ERROR."""
        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient") as mock_dav,
        ):
            result = tools_mod.caldav_delete_event(uid="x", calendar_name="team")
        assert result.status == Status.ERROR
        mock_dav.assert_not_called()

    def test_move_event_no_dots_error(self):
        """caldav_move_event with plain source_calendar → ERROR."""
        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient") as mock_dav,
        ):
            result = tools_mod.caldav_move_event(
                uid="x",
                target_calendar="work.nc.team",
                source_calendar="team",
            )
        assert result.status == Status.ERROR
        mock_dav.assert_not_called()

    def test_add_attendee_no_dots_error(self):
        """caldav_add_attendee with plain name → ERROR."""
        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient") as mock_dav,
        ):
            result = tools_mod.caldav_add_attendee(
                uid="x",
                email="a@b.com",
                calendar_name="team",
            )
        assert result.status == Status.ERROR
        mock_dav.assert_not_called()

    def test_remove_attendee_no_dots_error(self):
        """caldav_remove_attendee with plain name → ERROR."""
        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient") as mock_dav,
        ):
            result = tools_mod.caldav_remove_attendee(
                uid="x",
                email="a@b.com",
                calendar_name="team",
            )
        assert result.status == Status.ERROR
        mock_dav.assert_not_called()

    def test_list_attendees_no_dots_error(self):
        """caldav_list_attendees with plain name → ERROR."""
        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient") as mock_dav,
        ):
            result = tools_mod.caldav_list_attendees(uid="x", calendar_name="team")
        assert result.status == Status.ERROR
        mock_dav.assert_not_called()

    def test_empty_calendar_name_no_dots_error(self):
        """Empty calendar_name in pro mode write tool → ERROR (not a valid dotted path)."""
        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient") as mock_dav,
        ):
            result = tools_mod.caldav_create_event(
                summary="Test",
                start="2026-01-15T10:00",
                calendar_name="",
            )
        assert result.status == Status.ERROR
        mock_dav.assert_not_called()


# ===================================================================
# 8. Valid dotted path → client from stored creds, handler executes
# ===================================================================


class TestWriteToolValidDottedPath:
    """Test requirement 8: valid dotted path → DAVClient from stored creds."""

    def test_create_event_with_dotted_path(self):
        fake_cal = _fake_cal(name="team")
        fake_client = _fake_client(cal=fake_cal)
        recorder = mock.MagicMock(return_value=fake_client)

        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient", recorder),
            mock.patch("caldav_mcp.tools._get_calendar", return_value=fake_cal),
        ):
            result = tools_mod.caldav_create_event(
                summary="Sprint Planning",
                start="2026-01-15T10:00",
                calendar_name="work.nc.team",
            )
        assert result.status == Status.OK
        assert "Sprint Planning" in result.message
        # DAVClient was called with the remote's stored credentials
        recorder.assert_called_once()
        call_kwargs = recorder.call_args[1]
        assert call_kwargs["url"] == "https://cal.example/dav"
        assert call_kwargs["username"] == "alice"
        assert call_kwargs["password"] == "secret"

    def test_delete_event_with_dotted_path(self):
        fake_cal = _fake_cal(name="team")
        fake_event = mock.MagicMock()
        fake_cal.event_by_uid.return_value = fake_event
        fake_client = _fake_client(cal=fake_cal)

        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient", return_value=fake_client),
            mock.patch("caldav_mcp.tools._get_calendar", return_value=fake_cal),
        ):
            result = tools_mod.caldav_delete_event(
                uid="ev-1",
                calendar_name="work.nc.team",
            )
        assert result.status == Status.OK
        fake_event.delete.assert_called_once()


# ===================================================================
# 9. Non-granted config → generic ERROR, no client
# ===================================================================


class TestWriteToolNonGrantedConfig:
    """Test requirement 9: dotted path into non-granted config → ERROR."""

    def test_create_event_non_granted_config(self):
        """User 'work' has access to 'work' only; path into 'personal' → ERROR."""
        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient") as mock_dav,
        ):
            result = tools_mod.caldav_create_event(
                summary="Test",
                start="2026-01-15T10:00",
                calendar_name="personal.passthrough.mycal",
            )
        assert result.status == Status.ERROR
        mock_dav.assert_not_called()


# ===================================================================
# 10. Passthrough remote with/without headers
# ===================================================================


class TestPassthroughRemote:
    """Test requirement 10: passthrough remote in pro mode."""

    def test_passthrough_with_headers(self):
        """Passthrough remote with valid headers → DAVClient built from headers."""
        fake_cal = _fake_cal(name="mycal")
        fake_client = _fake_client(cal=fake_cal)
        recorder = mock.MagicMock(return_value=fake_client)
        headers = {
            "x-caldav-url": "https://pt.example/dav",
            "x-caldav-username": "pt-user",
            "x-caldav-password": "pt-pass",
        }

        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_BOTH)),
            mock.patch("caldav_mcp.tools.DAVClient", recorder),
            mock.patch("caldav_mcp.tools._get_calendar", return_value=fake_cal),
            mock.patch(
                "fastmcp.server.dependencies.get_http_headers",
                return_value=headers,
            ),
        ):
            result = tools_mod.caldav_create_event(
                summary="PT event",
                start="2026-01-15T10:00",
                calendar_name="personal.passthrough.mycal",
            )
        assert result.status == Status.OK
        recorder.assert_called_once()
        call_kwargs = recorder.call_args[1]
        assert call_kwargs["url"] == "https://pt.example/dav"
        assert call_kwargs["username"] == "pt-user"
        assert call_kwargs["password"] == "pt-pass"

    def test_passthrough_missing_headers(self):
        """Passthrough remote without required headers → AuthError (caught by _REMOTE_ERRORS)."""
        empty_headers: dict[str, str] = {}

        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_BOTH)),
            mock.patch("caldav_mcp.tools.DAVClient") as mock_dav,
            mock.patch(
                "fastmcp.server.dependencies.get_http_headers",
                return_value=empty_headers,
            ),
        ):
            result = tools_mod.caldav_create_event(
                summary="PT event",
                start="2026-01-15T10:00",
                calendar_name="personal.passthrough.mycal",
            )
        assert result.status == Status.AUTH
        assert "X-Caldav" in result.message
        mock_dav.assert_not_called()


# ===================================================================
# 11. Cache reuse across calls
# ===================================================================


class TestCacheReuse:
    """Test requirement 11: same calendar → single DAVClient; second remote → second client."""

    def test_same_remote_reuses_cached_client(self):
        """Two calls to the same remote → only one DAVClient construction."""
        fake_cal = _fake_cal(name="team")
        fake_client = _fake_client(cal=fake_cal)
        recorder = mock.MagicMock(return_value=fake_client)

        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient", recorder),
            mock.patch("caldav_mcp.tools._get_calendar", return_value=fake_cal),
        ):
            tools_mod.caldav_create_event(
                summary="E1",
                start="2026-01-15T10:00",
                calendar_name="work.nc.team",
            )
            tools_mod.caldav_create_event(
                summary="E2",
                start="2026-01-15T11:00",
                calendar_name="work.nc.personal",
            )
        # Only one DAVClient built (same remote url+username → cache hit)
        assert recorder.call_count == 1

    def test_different_remote_builds_second_client(self):
        """Calls to two different remotes → two DAVClient constructions."""
        cal1 = _fake_cal(name="team")
        cal2 = _fake_cal(name="shared")
        client1 = _fake_client(cal=cal1)
        client2 = _fake_client(cal=cal2)
        recorder = mock.MagicMock(side_effect=[client1, client2])

        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient", recorder),
            mock.patch("caldav_mcp.tools._get_calendar", side_effect=[cal1, cal2]),
        ):
            tools_mod.caldav_create_event(
                summary="E1",
                start="2026-01-15T10:00",
                calendar_name="work.nc.team",
            )
            tools_mod.caldav_create_event(
                summary="E2",
                start="2026-01-15T11:00",
                calendar_name="work.nc2.shared",
            )
        assert recorder.call_count == 2


# ===================================================================
# 12. caldav_move_event pro mode
# ===================================================================


class TestMoveEventProMode:
    """Test requirement 12: move_event with both dotted paths, cross-config target."""

    def test_move_cross_config_target_resolved(self):
        """Target in another granted config → both clients resolved."""
        from datetime import datetime

        src_cal = _fake_cal(name="team")
        dst_cal = _fake_cal(name="mycal")
        src_client = _fake_client(cal=src_cal)
        dst_client = _fake_client(cal=dst_cal)

        # Build a proper icalendar event for the move handler
        from icalendar import Calendar as ICalCalendar
        from icalendar import Event as ICalEvent

        ical = ICalCalendar()
        ev = ICalEvent()
        ev.add("uid", "ev-1")
        ev.add("summary", "Test")
        ev.add("dtstart", datetime(2026, 1, 15, 10, 0))
        ical.add_component(ev)

        fake_event = mock.MagicMock()
        fake_event.icalendar_component = ev
        fake_event.data = ical.to_ical().decode("utf-8")

        src_cal.event_by_uid.return_value = fake_event

        src_client_call_count = 0
        dst_client_call_count = 0

        def make_client(url="", username="", password="", **kwargs):
            nonlocal src_client_call_count, dst_client_call_count
            if "cal.example" in url:
                src_client_call_count += 1
                return src_client
            elif "pt.example" in url:
                dst_client_call_count += 1
                return dst_client
            return mock.MagicMock()

        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_BOTH)),
            mock.patch("caldav_mcp.tools.DAVClient", side_effect=make_client),
            # _get_calendar is imported directly in mutations.py
            mock.patch("caldav_mcp.tools.mutations._get_calendar", side_effect=[src_cal, dst_cal]),
            # _resolve_pro_remote_client is called in the handler for the target
            mock.patch("caldav_mcp.tools._resolve_pro_remote_client", return_value=dst_client),
        ):
            result = tools_mod.caldav_move_event(
                uid="ev-1",
                source_calendar="work.nc.team",
                target_calendar="personal.passthrough.mycal",
            )
        assert result.status == Status.OK, f"Got: {result.status} — {result.message}"
        assert src_client_call_count == 1

    def test_move_target_no_access_error(self):
        """Target path without access → ERROR."""
        src_cal = _fake_cal(name="team")
        src_client = _fake_client(cal=src_cal)

        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient", return_value=src_client),
            mock.patch("caldav_mcp.tools.mutations._get_calendar", return_value=src_cal),
        ):
            result = tools_mod.caldav_move_event(
                uid="ev-1",
                source_calendar="work.nc.team",
                target_calendar="personal.passthrough.mycal",
            )
        # User WORK has access to 'work' only; target is in 'personal' config.
        # resolve_addressed_calendar raises ValueError → Status.ERROR.
        assert result.status == Status.ERROR, f"Got: {result.status} — {result.message}"


# ===================================================================
# 13. Read tool dotted path resolves (interim), plain name errors
# ===================================================================


class TestReadToolProMode:
    """Test requirement 13: read tool with dotted path resolves; plain name errors."""

    def test_get_events_dotted_path_resolves(self):
        """caldav_get_events with dotted path in pro mode → resolves and executes."""
        fake_cal = _fake_cal(name="team")
        fake_client = _fake_client(cal=fake_cal)

        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient", return_value=fake_client),
            mock.patch("caldav_mcp.tools._get_calendar", return_value=fake_cal),
        ):
            result = tools_mod.caldav_get_events(calendar_name="work.nc.team")
        # The handler executes (may return EMPTY if no events, but no error)
        assert result.status in (Status.OK, Status.EMPTY)

    def test_get_events_plain_name_in_pro_mode(self):
        """caldav_get_events with plain name in pro mode → generic ValueError."""
        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=_auth_return(_USER_WORK)),
            mock.patch("caldav_mcp.tools.DAVClient") as mock_dav,
        ):
            result = tools_mod.caldav_get_events(calendar_name="team")
        # Read tools don't have write=True, so no dotted-path validation;
        # but _resolve_client_and_calendar tries to resolve the path via the
        # pro branch, which calls resolve_addressed_calendar with the plain
        # name — this raises ValueError → Status.ERROR via _render_error.
        assert result.status == Status.ERROR
        mock_dav.assert_not_called()


# ===================================================================
# 14. Simple-mode guard: write tools with plain names still work
# ===================================================================


class TestSimpleModeWriteTools:
    """Test requirement 14: simple-mode write tools with plain names still work."""

    def test_simple_mode_create_event_with_plain_name(self):
        """In simple mode (pro_user=None), plain calendar_name still works."""
        fake_cal = _fake_cal(name="Work")
        fake_client = _fake_client(cal=fake_cal)
        recorder = mock.MagicMock(return_value=fake_client)

        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=(None, None)),
            mock.patch("caldav_mcp.tools._resolve_credentials", return_value=("u", "p", "w")),
            mock.patch("caldav_mcp.tools.DAVClient", recorder),
            mock.patch("caldav_mcp.tools._get_calendar", return_value=fake_cal),
        ):
            result = tools_mod.caldav_create_event(
                summary="Meeting",
                start="2026-01-15T10:00",
                calendar_name="Work",
            )
        assert result.status == Status.OK
        # Simple mode: no dotted-path validation
        assert "Meeting" in result.message

    def test_simple_mode_move_event_with_plain_names(self):
        """In simple mode, move_event with plain target_calendar still works."""
        src_cal = _fake_cal(name="src")
        dst_cal = _fake_cal(name="dst")
        fake_client = _fake_client(cal=src_cal)

        # Build a proper icalendar event
        from datetime import datetime

        from icalendar import Calendar as ICalCalendar
        from icalendar import Event as ICalEvent

        ical = ICalCalendar()
        ev = ICalEvent()
        ev.add("uid", "ev-1")
        ev.add("summary", "Test")
        ev.add("dtstart", datetime(2026, 1, 15, 10, 0))
        ical.add_component(ev)

        fake_event = mock.MagicMock()
        fake_event.icalendar_component = ev
        fake_event.data = ical.to_ical().decode("utf-8")

        src_cal.event_by_uid.return_value = fake_event

        with (
            mock.patch("caldav_mcp.tools._authenticate", return_value=(None, None)),
            mock.patch("caldav_mcp.tools._resolve_credentials", return_value=("u", "p", "w")),
            mock.patch("caldav_mcp.tools.DAVClient", return_value=fake_client),
            # _get_calendar is imported directly in mutations.py
            mock.patch("caldav_mcp.tools.mutations._get_calendar", side_effect=[src_cal, dst_cal]),
        ):
            result = tools_mod.caldav_move_event(
                uid="ev-1",
                target_calendar="dst",
                source_calendar="src",
            )
        assert result.status == Status.OK, f"Got: {result.status} — {result.message}"
