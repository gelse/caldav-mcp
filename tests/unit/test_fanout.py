"""Unit tests for the fan-out executor and scope computation (M4.4, M5.1).

Tests 1–7 from the original plan plus M5.1 requirements 1–6.  All tests
use fabricated ``AppConfig`` objects and stub ``query_fn`` callables —
no network, no real DAVClient.
"""

from __future__ import annotations

from unittest import mock

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
from caldav_mcp.errors import AuthError, NotFoundError, Status
from caldav_mcp.fanout import (
    AggregatedEntry,
    _classify_exception,
    accessible_scopes,
    aggregate_remote_status,
    render_fanout_message,
    run_fanout,
)
from caldav_mcp.tools import get_cache

# ---------------------------------------------------------------------------
# Fabricated data
# ---------------------------------------------------------------------------

_REMOTE_A = Remote(
    name="remote-a",
    url="https://a.example/dav",
    auth_mode="direct",
    username="userA",
    password="passA",
)
_REMOTE_B = Remote(
    name="remote-b",
    url="https://b.example/dav",
    auth_mode="direct",
    username="userB",
    password="passB",
)
_REMOTE_PT = Remote(
    name="remote-pt",
    url="",
    auth_mode="passthrough",
)

_CONFIG_ALPHA = Config(
    name="alpha",
    remotes=(_REMOTE_A, _REMOTE_B),
    calendars=(
        ("remote-a", (Calendar(name="cal-a1"), Calendar(name="cal-a2"))),
        ("remote-b", (Calendar(name="cal-b1"),)),
    ),
)

_CONFIG_BETA = Config(
    name="beta",
    remotes=(_REMOTE_A,),
    calendars=(("remote-a", (Calendar(name="cal-ba1"),)),),
)

_CONFIG_GAMMA = Config(
    name="gamma",
    remotes=(_REMOTE_PT,),
    calendars=(("remote-pt", (Calendar(name="cal-pt1"),)),),
)

_APP = AppConfig(
    mode="db",
    config=None,
    configs=(_CONFIG_ALPHA, _CONFIG_BETA, _CONFIG_GAMMA),
)

_USER_ALL = ProUser(username="all", key_hash="h1", config_names=("alpha", "beta", "gamma"))
_USER_ALPHA_ONLY = ProUser(username="alpha", key_hash="h2", config_names=("alpha",))
_USER_NONE = ProUser(username="none", key_hash="h3", config_names=())
_USER_BETA_GAMMA = ProUser(username="bg", key_hash="h4", config_names=("beta", "gamma"))


# ---------------------------------------------------------------------------
# Test 1: accessible_scopes filters by user's config names
# ---------------------------------------------------------------------------


class TestAccessibleScopes:
    def test_filters_by_user_config_names(self):
        """Scopes only include configs the user is granted access to."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)
        assert len(scopes) == 2  # alpha has 2 remotes
        assert all(s.config_name == "alpha" for s in scopes)

    def test_preserves_declaration_order(self):
        """Scope order matches config/remote declaration order."""
        scopes = accessible_scopes(_APP, _USER_ALL)
        config_names = [s.config_name for s in scopes]
        assert config_names == ["alpha", "alpha", "beta", "gamma"]

    def test_none_user_returns_empty(self):
        """None user → empty scopes (defensive)."""
        assert accessible_scopes(_APP, None) == ()

    def test_user_with_no_grants_returns_empty(self):
        """User granted no configs → empty scopes."""
        assert accessible_scopes(_APP, _USER_NONE) == ()

    def test_beta_gamma_user(self):
        """User with only beta+gamma → scopes from those configs."""
        scopes = accessible_scopes(_APP, _USER_BETA_GAMMA)
        config_names = [s.config_name for s in scopes]
        assert config_names == ["beta", "gamma"]

    def test_calendar_names_populated(self):
        """Scopes carry the correct calendar names from the config."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)
        # First remote in alpha
        assert scopes[0].calendar_names == ("cal-a1", "cal-a2")
        # Second remote in alpha
        assert scopes[1].calendar_names == ("cal-b1",)


# ---------------------------------------------------------------------------
# Test 2: Happy path — all scopes succeed
# ---------------------------------------------------------------------------


class TestFanoutHappyPath:
    def test_all_succeed(self):
        """All scopes succeed → Status.OK with entries in order."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)

        def query_fn(scope, cal_name):
            return AggregatedEntry(
                config_name=scope.config_name,
                remote_name=scope.remote.name,
                calendar_name=cal_name or "",
                data={"events": []},
            )

        result = run_fanout(scopes, {}, query_fn)
        assert result.status == Status.OK
        assert len(result.entries) == 3  # 2+1 calendars
        # Order matches scope/calendar iteration
        assert result.entries[0].config_name == "alpha"
        assert result.entries[0].remote_name == "remote-a"
        assert result.entries[0].calendar_name == "cal-a1"
        assert result.entries[1].calendar_name == "cal-a2"
        assert result.entries[2].calendar_name == "cal-b1"

    def test_entry_data_carried(self):
        """Data from the handler is carried in each entry."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)

        def query_fn(scope, cal_name):
            return AggregatedEntry(
                config_name=scope.config_name,
                remote_name=scope.remote.name,
                calendar_name=cal_name or "",
                data={"count": 42},
            )

        result = run_fanout(scopes, {}, query_fn)
        assert all(e.data == {"count": 42} for e in result.entries)


# ---------------------------------------------------------------------------
# Test 3: One scope raises → entry carries error, others succeed
# ---------------------------------------------------------------------------


class TestFanoutPartialFailure:
    def test_one_scope_errors(self):
        """One scope raises → error entry, others OK, top-level Status.OK."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)
        call_count = 0

        def query_fn(scope, cal_name):
            nonlocal call_count
            call_count += 1
            if scope.remote.name == "remote-a" and cal_name == "cal-a2":
                raise RuntimeError("connection refused")
            return AggregatedEntry(
                config_name=scope.config_name,
                remote_name=scope.remote.name,
                calendar_name=cal_name or "",
                data={"ok": True},
            )

        result = run_fanout(scopes, {}, query_fn)
        assert result.status == Status.OK  # at least one succeeded
        assert call_count == 3
        # The failing entry has an error
        error_entries = [e for e in result.entries if e.error is not None]
        assert len(error_entries) == 1
        assert error_entries[0].calendar_name == "cal-a2"
        assert "connection refused" in error_entries[0].error


# ---------------------------------------------------------------------------
# Test 4: All scopes raise → Status.ERROR
# ---------------------------------------------------------------------------


class TestFanoutAllError:
    def test_all_error(self):
        """All scopes raise → Status.ERROR, message names failing remotes."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)

        def query_fn(scope, cal_name):
            raise RuntimeError("down")

        result = run_fanout(scopes, {}, query_fn)
        assert result.status == Status.ERROR
        assert "remote-a" in result.message.lower()
        assert "remote-b" in result.message.lower()
        assert len(result.entries) == 3
        assert all(e.error is not None for e in result.entries)


# ---------------------------------------------------------------------------
# Test 5: Zero accessible configs → Status.EMPTY
# ---------------------------------------------------------------------------


class TestFanoutEmpty:
    def test_zero_scopes(self):
        """Zero accessible scopes → Status.EMPTY."""
        result = run_fanout((), {}, lambda s, c: None)
        assert result.status == Status.EMPTY
        assert len(result.entries) == 0

    def test_none_user_empty_scopes(self):
        """accessible_scopes with None user → empty → run_fanout → EMPTY."""
        scopes = accessible_scopes(_APP, None)
        result = run_fanout(scopes, {}, lambda s, c: None)
        assert result.status == Status.EMPTY


# ---------------------------------------------------------------------------
# Test 6: Passthrough scope with/without headers
# ---------------------------------------------------------------------------


class TestFanoutPassthrough:
    def test_passthrough_with_headers(self):
        """Passthrough remote with headers → query executed."""
        scopes = accessible_scopes(_APP, _USER_BETA_GAMMA)
        pt_scopes = [s for s in scopes if s.config_name == "gamma"]
        assert len(pt_scopes) == 1

        def query_fn(scope, cal_name):
            return AggregatedEntry(
                config_name=scope.config_name,
                remote_name=scope.remote.name,
                calendar_name=cal_name or "",
                data={"source": "headers"},
            )

        headers = {
            "X-Caldav-Url": "https://pt.example/dav",
            "X-Caldav-Username": "ptuser",
            "X-Caldav-Password": "ptpass",
        }
        with mock.patch(
            "caldav_mcp.tools._resolve_pro_client_for_scope",
            return_value=mock.MagicMock(),
        ):
            result = run_fanout(tuple(pt_scopes), headers, query_fn)
        assert result.status == Status.OK

    def test_passthrough_client_error(self):
        """Passthrough remote without headers → AuthError captured as entry error."""
        scopes = accessible_scopes(_APP, _USER_BETA_GAMMA)
        pt_scopes = [s for s in scopes if s.config_name == "gamma"]

        def query_fn(scope, cal_name):
            return AggregatedEntry(
                config_name=scope.config_name,
                remote_name=scope.remote.name,
                calendar_name=cal_name or "",
                data={"ok": True},
            )

        # No headers → _resolve_pro_client_for_scope will raise AuthError
        with mock.patch(
            "caldav_mcp.tools._resolve_pro_client_for_scope",
            side_effect=RuntimeError("Missing CalDAV credentials"),
        ):
            result = run_fanout(tuple(pt_scopes), {}, query_fn)
        assert result.status == Status.ERROR
        assert all(e.error is not None for e in result.entries)


# ---------------------------------------------------------------------------
# Test 7: Sequential ordering
# ---------------------------------------------------------------------------


class TestFanoutSequentialOrdering:
    def test_invocation_order_matches_scope_order(self):
        """query_fn is called in scope/calendar declaration order."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)
        invocation_order = []

        def query_fn(scope, cal_name):
            invocation_order.append((scope.config_name, scope.remote.name, cal_name))
            return AggregatedEntry(
                config_name=scope.config_name,
                remote_name=scope.remote.name,
                calendar_name=cal_name or "",
                data=None,
            )

        run_fanout(scopes, {}, query_fn)

        expected = [
            ("alpha", "remote-a", "cal-a1"),
            ("alpha", "remote-a", "cal-a2"),
            ("alpha", "remote-b", "cal-b1"),
        ]
        assert invocation_order == expected

    def test_client_error_before_query_fn(self):
        """Client resolution error skips query_fn for that scope's calendars."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)
        invocation_order = []

        def query_fn(scope, cal_name):
            invocation_order.append((scope.remote.name, cal_name))
            return AggregatedEntry(
                config_name=scope.config_name,
                remote_name=scope.remote.name,
                calendar_name=cal_name or "",
                data=None,
            )

        # Patch _resolve_pro_client_for_scope to fail on remote-a
        with mock.patch(
            "caldav_mcp.tools._resolve_pro_client_for_scope",
            side_effect=lambda remote, headers: (
                (_ for _ in ()).throw(RuntimeError("fail"))
                if remote.name == "remote-a"
                else mock.MagicMock()
            ),
        ):
            result = run_fanout(scopes, {}, query_fn)

        # Only remote-b calendars were queried
        assert invocation_order == [("remote-b", "cal-b1")]
        # remote-a entries have errors
        a_errors = [e for e in result.entries if e.remote_name == "remote-a"]
        assert len(a_errors) == 2
        assert all(e.error is not None for e in a_errors)


# ---------------------------------------------------------------------------
# M5.1 Requirement 1: Mixed success/failure with status field
# ---------------------------------------------------------------------------


class TestFanoutMixedSuccessFailure:
    def test_mixed_success_failure_entries(self):
        """3 scopes, middle one raises → status ok/error/ok, top-level Status.OK."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)
        call_count = 0

        def query_fn(scope, cal_name):
            nonlocal call_count
            call_count += 1
            if scope.remote.name == "remote-a" and cal_name == "cal-a2":
                raise RuntimeError("connection refused")
            return AggregatedEntry(
                config_name=scope.config_name,
                remote_name=scope.remote.name,
                calendar_name=cal_name or "",
                data={"events": []},
                status="ok",
            )

        result = run_fanout(scopes, {}, query_fn)
        assert result.status == Status.OK
        assert call_count == 3

        # Check statuses in order
        assert result.entries[0].status == "ok"
        assert result.entries[1].status == "error"
        assert result.entries[2].status == "ok"

        # Failing entry has non-empty error
        assert result.entries[1].error is not None
        assert "connection refused" in result.entries[1].error

        # Succeeding entries have data
        assert result.entries[0].data == {"events": []}
        assert result.entries[2].data == {"events": []}

    def test_status_field_defaults_to_ok(self):
        """AggregatedEntry defaults to status='ok'."""
        entry = AggregatedEntry(
            config_name="c",
            remote_name="r",
            calendar_name="cal",
            data={"x": 1},
        )
        assert entry.status == "ok"


# ---------------------------------------------------------------------------
# M5.1 Requirement 2: Failure taxonomy per exception type
# ---------------------------------------------------------------------------


class TestFanoutFailureTaxonomy:
    def test_auth_error_gives_status_auth(self):
        """AuthError → entry status='auth'."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)

        def query_fn(scope, cal_name):
            raise AuthError("missing credentials")

        result = run_fanout(scopes, {}, query_fn)
        assert result.status == Status.ERROR
        assert all(e.status == "auth" for e in result.entries)
        assert all(e.error is not None for e in result.entries)

    def test_not_found_error_gives_status_not_found(self):
        """NotFoundError → entry status='not_found'."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)

        def query_fn(scope, cal_name):
            raise NotFoundError("calendar not found")

        result = run_fanout(scopes, {}, query_fn)
        assert result.status == Status.ERROR
        assert all(e.status == "not_found" for e in result.entries)
        assert all(e.error is not None for e in result.entries)

    def test_generic_error_gives_status_error(self):
        """Generic RuntimeError → entry status='error'."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)

        def query_fn(scope, cal_name):
            raise RuntimeError("down")

        result = run_fanout(scopes, {}, query_fn)
        assert result.status == Status.ERROR
        assert all(e.status == "error" for e in result.entries)
        assert all(e.error is not None for e in result.entries)

    def test_classify_exception_helper(self):
        """_classify_exception maps exceptions correctly."""
        assert _classify_exception(AuthError("x")) == "auth"
        assert _classify_exception(NotFoundError("x")) == "not_found"
        assert _classify_exception(RuntimeError("x")) == "error"
        assert _classify_exception(ValueError("x")) == "error"


# ---------------------------------------------------------------------------
# M5.1 Requirement 3: All scopes fail → Status.ERROR with status lines
# ---------------------------------------------------------------------------


class TestFanoutAllFail:
    def test_all_fail_error_status(self):
        """All scopes fail → Status.ERROR, message lists every remote."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)

        def query_fn(scope, cal_name):
            raise RuntimeError("down")

        result = run_fanout(scopes, {}, query_fn)
        assert result.status == Status.ERROR
        assert len(result.entries) == 3
        assert all(e.status == "error" for e in result.entries)
        # Message should contain remote references
        assert "remote-a" in result.message.lower()
        assert "remote-b" in result.message.lower()


# ---------------------------------------------------------------------------
# M5.1 Requirement 4: Empty remote among successes
# ---------------------------------------------------------------------------


class TestFanoutEmptyAmongSuccess:
    def test_empty_remote_counts_as_ok(self):
        """One scope returns empty data → status='empty', top-level OK."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)

        def query_fn(scope, cal_name):
            if scope.remote.name == "remote-a" and cal_name == "cal-a2":
                return AggregatedEntry(
                    config_name=scope.config_name,
                    remote_name=scope.remote.name,
                    calendar_name=cal_name or "",
                    data=[],
                    status="empty",
                )
            return AggregatedEntry(
                config_name=scope.config_name,
                remote_name=scope.remote.name,
                calendar_name=cal_name or "",
                data={"events": [{"uid": "1"}]},
                status="ok",
            )

        result = run_fanout(scopes, {}, query_fn)
        assert result.status == Status.OK
        # Empty entry should be marked as empty
        empty_entries = [e for e in result.entries if e.status == "empty"]
        assert len(empty_entries) == 1
        # Message should count empty as ok
        assert "ok" in result.message.lower()


# ---------------------------------------------------------------------------
# M5.1 Requirement 5: Passthrough scope missing headers → auth
# ---------------------------------------------------------------------------


class TestFanoutPassthroughStatus:
    def test_passthrough_missing_headers_gives_auth_status(self):
        """Passthrough scope without headers → status='auth'."""
        scopes = accessible_scopes(_APP, _USER_BETA_GAMMA)
        pt_scopes = [s for s in scopes if s.config_name == "gamma"]

        def query_fn(scope, cal_name):
            return AggregatedEntry(
                config_name=scope.config_name,
                remote_name=scope.remote.name,
                calendar_name=cal_name or "",
                data={"ok": True},
            )

        # No headers → _resolve_pro_client_for_scope will raise AuthError
        with mock.patch(
            "caldav_mcp.tools._resolve_pro_client_for_scope",
            side_effect=AuthError("Missing CalDAV credentials"),
        ):
            result = run_fanout(tuple(pt_scopes), {}, query_fn)
        assert result.status == Status.ERROR
        assert all(e.status == "auth" for e in result.entries)
        assert all(e.error is not None for e in result.entries)

    def test_passthrough_with_headers_gives_ok_status(self):
        """Passthrough remote with headers → status='ok'."""
        scopes = accessible_scopes(_APP, _USER_BETA_GAMMA)
        pt_scopes = [s for s in scopes if s.config_name == "gamma"]

        def query_fn(scope, cal_name):
            return AggregatedEntry(
                config_name=scope.config_name,
                remote_name=scope.remote.name,
                calendar_name=cal_name or "",
                data={"source": "headers"},
                status="ok",
            )

        headers = {
            "X-Caldav-Url": "https://pt.example/dav",
            "X-Caldav-Username": "ptuser",
            "X-Caldav-Password": "ptpass",
        }
        with mock.patch(
            "caldav_mcp.tools._resolve_pro_client_for_scope",
            return_value=mock.MagicMock(),
        ):
            result = run_fanout(tuple(pt_scopes), headers, query_fn)
        assert result.status == Status.OK
        assert all(e.status == "ok" for e in result.entries)


# ---------------------------------------------------------------------------
# M5.1 Requirement 6: Audit remotes map
# ---------------------------------------------------------------------------


class TestFanoutAuditRemotes:
    def test_render_fanout_message_all_ok(self):
        """render_fanout_message with all-ok entries produces correct first line."""
        entries = (
            AggregatedEntry(
                config_name="c1",
                remote_name="r1",
                calendar_name="cal1",
                data={"events": []},
                status="ok",
            ),
            AggregatedEntry(
                config_name="c1",
                remote_name="r2",
                calendar_name="cal2",
                data={"events": []},
                status="ok",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        assert msg.startswith("OK ")
        assert "2 ok" in msg
        assert "0 error" not in msg

    def test_render_fanout_message_mixed(self):
        """render_fanout_message with mixed entries shows counts."""
        entries = (
            AggregatedEntry(
                config_name="c1",
                remote_name="r1",
                calendar_name="cal1",
                data={"events": []},
                status="ok",
            ),
            AggregatedEntry(
                config_name="c1",
                remote_name="r2",
                calendar_name="cal2",
                error="connection refused",
                status="error",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        assert "1 ok" in msg
        assert "1 error" in msg
        assert "- [ok]" in msg
        assert "- [error]" in msg

    def test_render_fanout_message_all_failed(self):
        """render_fanout_message with all-failed entries."""
        entries = (
            AggregatedEntry(
                config_name="c1",
                remote_name="r1",
                calendar_name="cal1",
                error="down",
                status="error",
            ),
            AggregatedEntry(
                config_name="c1",
                remote_name="r2",
                calendar_name="cal2",
                error="down",
                status="error",
            ),
        )
        msg = render_fanout_message(entries, Status.ERROR)
        assert msg.startswith("ERROR:[server]")
        assert "2 error" in msg
        assert "- [error]" in msg

    def test_render_fanout_message_empty_entries(self):
        """render_fanout_message with empty entries."""
        msg = render_fanout_message((), Status.EMPTY)
        assert msg == "No accessible calendars"


# ---------------------------------------------------------------------------
# M5.1 Requirement 6 (integration): exactly one audit entry per fan-out call,
# carrying a "remotes" map with one key per remote and its status string.
# ---------------------------------------------------------------------------


class TestFanoutAuditRemotesIntegration:
    def test_one_log_entry_with_per_remote_status_map(self):
        """A fan-out tool call logs once with one remotes key per remote."""
        configure_app_config(_APP)
        get_cache().clear()

        def _get_cal(client, calendar_name=None):
            if calendar_name == "cal-b1":
                raise NotFoundError("Calendar 'cal-b1' not found")
            return mock.MagicMock()

        try:
            with (
                mock.patch(
                    "caldav_mcp.tools._authenticate",
                    return_value=(_USER_ALPHA_ONLY, None),
                ),
                mock.patch("caldav_mcp.tools.DAVClient", return_value=mock.MagicMock()),
                mock.patch("caldav_mcp.calendar._get_calendar", side_effect=_get_cal),
                mock.patch("caldav_mcp.tools.log_operation") as mock_log,
            ):
                result = tools_mod.caldav_get_events()
        finally:
            reset_app_config()
            get_cache().clear()

        assert result.status == Status.OK
        assert mock_log.call_count == 1
        remotes = mock_log.call_args.kwargs["remotes"]
        # remote-a has two calendars, both returning empty events → "empty"
        # entries.  aggregate_remote_status treats "empty" as non-failure,
        # so the remote's aggregated status is "ok".
        assert dict(remotes) == {
            "alpha.remote-a": "ok",
            "alpha.remote-b": "not_found",
        }
        assert result.message.startswith("OK Fan-out across 3 scopes: 2 ok, 1 error")


# ---------------------------------------------------------------------------
# D1/D2 regression: mixed-per-calendar — one calendar ok, another error
# on the same remote → render uses vocabulary status, audit map reflects
# the failure.
# ---------------------------------------------------------------------------


class TestFanoutMixedPerCalendar:
    """A remote with two calendars: one succeeds, one fails.

    Verifies:
    - The render line uses a vocabulary status (never ``[mixed]``).
    - The audit ``remotes`` map reflects the failure for the remote.
    - The top-level status is ``Status.OK`` (at least one scope succeeded).
    """

    def test_render_uses_vocabulary_status_not_mixed(self):
        """Mixed per-calendar entries → render picks the worst status."""
        entries = (
            AggregatedEntry(
                config_name="alpha",
                remote_name="remote-a",
                calendar_name="cal-a1",
                data={"events": [{"uid": "1"}]},
                status="ok",
            ),
            AggregatedEntry(
                config_name="alpha",
                remote_name="remote-a",
                calendar_name="cal-a2",
                error="connection refused",
                status="error",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        assert "[mixed]" not in msg
        assert "[error]" in msg
        assert "1 ok, 1 error" in msg

    def test_run_fanout_mixed_per_calendar(self):
        """run_fanout with mixed per-calendar results → correct entries and status."""
        scopes = accessible_scopes(_APP, _USER_ALPHA_ONLY)

        def query_fn(scope, cal_name):
            if scope.remote.name == "remote-a" and cal_name == "cal-a2":
                raise RuntimeError("connection refused")
            return AggregatedEntry(
                config_name=scope.config_name,
                remote_name=scope.remote.name,
                calendar_name=cal_name or "",
                data={"events": [{"uid": "1"}]},
                status="ok",
            )

        result = run_fanout(scopes, {}, query_fn)
        assert result.status == Status.OK
        # Entry statuses: ok, error, ok
        assert result.entries[0].status == "ok"
        assert result.entries[1].status == "error"
        assert result.entries[2].status == "ok"
        # Render message must not contain [mixed]
        assert "[mixed]" not in result.message
        assert "[error]" in result.message

    def test_aggregate_remote_status_picks_worst(self):
        """aggregate_remote_status returns the most severe status."""
        entries = [
            AggregatedEntry(
                config_name="c",
                remote_name="r",
                calendar_name="cal1",
                data={"x": 1},
                status="ok",
            ),
            AggregatedEntry(
                config_name="c",
                remote_name="r",
                calendar_name="cal2",
                error="down",
                status="error",
            ),
        ]
        assert aggregate_remote_status(entries) == "error"

    def test_aggregate_remote_status_all_ok(self):
        """aggregate_remote_status returns 'ok' when all entries are ok/empty."""
        entries = [
            AggregatedEntry(
                config_name="c",
                remote_name="r",
                calendar_name="cal1",
                data={"x": 1},
                status="ok",
            ),
            AggregatedEntry(
                config_name="c",
                remote_name="r",
                calendar_name="cal2",
                data=[],
                status="empty",
            ),
        ]
        assert aggregate_remote_status(entries) == "ok"

    def test_aggregate_remote_status_auth_wins(self):
        """aggregate_remote_status returns 'auth' when mixed with ok."""
        entries = [
            AggregatedEntry(
                config_name="c",
                remote_name="r",
                calendar_name="cal1",
                data={"x": 1},
                status="ok",
            ),
            AggregatedEntry(
                config_name="c",
                remote_name="r",
                calendar_name="cal2",
                error="missing creds",
                status="auth",
            ),
        ]
        assert aggregate_remote_status(entries) == "auth"

    def test_aggregate_remote_status_not_found_wins(self):
        """aggregate_remote_status returns 'not_found' when mixed with empty."""
        entries = [
            AggregatedEntry(
                config_name="c",
                remote_name="r",
                calendar_name="cal1",
                data=[],
                status="empty",
            ),
            AggregatedEntry(
                config_name="c",
                remote_name="r",
                calendar_name="cal2",
                error="not found",
                status="not_found",
            ),
        ]
        assert aggregate_remote_status(entries) == "not_found"

    def test_audit_remotes_map_reflects_failure(self):
        """In a mixed per-calendar fan-out, the audit remotes map
        shows the aggregated (worst) status for the remote, not the
        first calendar's status."""
        configure_app_config(_APP)
        get_cache().clear()

        call_count = 0

        def _get_cal(client, calendar_name=None):
            nonlocal call_count
            call_count += 1
            # cal-a2 is the second call for remote-a
            if calendar_name == "cal-a2":
                raise NotFoundError("Calendar 'cal-a2' not found")
            return mock.MagicMock()

        try:
            with (
                mock.patch(
                    "caldav_mcp.tools._authenticate",
                    return_value=(_USER_ALPHA_ONLY, None),
                ),
                mock.patch("caldav_mcp.tools.DAVClient", return_value=mock.MagicMock()),
                mock.patch("caldav_mcp.calendar._get_calendar", side_effect=_get_cal),
                mock.patch("caldav_mcp.tools.log_operation") as mock_log,
            ):
                result = tools_mod.caldav_get_events()
        finally:
            reset_app_config()
            get_cache().clear()

        assert result.status == Status.OK
        remotes = mock_log.call_args.kwargs["remotes"]
        # remote-a's first calendar (cal-a1) succeeds → "empty" (no events),
        # but remote-a's second calendar (cal-a2) is not_found.
        # The aggregated status must be "not_found", not first-wins "empty".
        assert dict(remotes)["alpha.remote-a"] == "not_found"
