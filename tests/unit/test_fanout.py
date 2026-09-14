"""Unit tests for the fan-out executor and scope computation (M4.4).

Tests 1–7 from the plan.  All tests use fabricated ``AppConfig`` objects
and stub ``query_fn`` callables — no network, no real DAVClient.
"""

from __future__ import annotations

from unittest import mock

from caldav_mcp.app_config import (
    AppConfig,
    Calendar,
    Config,
    Remote,
)
from caldav_mcp.db_loader import ProUser
from caldav_mcp.errors import Status
from caldav_mcp.fanout import (
    AggregatedEntry,
    accessible_scopes,
    run_fanout,
)

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
