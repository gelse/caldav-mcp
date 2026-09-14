"""Unit tests for dotted-path addressing and access filtering (M4.3).

Covers:
  1. ``parse_dotted_path`` edge cases
  2. ``resolve_addressed_calendar`` happy path (fabricated db-mode AppConfig)
  3. Unknown config / remote / calendar → identical ValueError
  4. Access-denied user gets the same error as not-found (no enumeration)
  5. ``user=None`` (simple-mode shape) skips access filtering
  6. Frozen ``CalendarAddress`` / ``Resolution`` records
"""

from __future__ import annotations

import pytest

from caldav_mcp.addressing import (
    CalendarAddress,
    Resolution,
    parse_dotted_path,
    resolve_addressed_calendar,
)
from caldav_mcp.app_config import (
    AppConfig,
    Calendar,
    Config,
    Remote,
)
from caldav_mcp.db_loader import ProUser

# ---------------------------------------------------------------------------
# Fabricated db-mode AppConfig: 2 configs × 2 remotes × 2 calendars
# ---------------------------------------------------------------------------

_REMOTE_A1 = Remote(
    name="remote-a1",
    url="https://a1.cal.example/dav",
    auth_mode="direct",
    username="alice",
    password="pass-a1",
)
_REMOTE_A2 = Remote(
    name="remote-a2",
    url="https://a2.cal.example/dav",
    auth_mode="direct",
    username="alice",
    password="pass-a2",
)
_REMOTE_B1 = Remote(
    name="remote-b1",
    url="https://b1.cal.example/dav",
    auth_mode="direct",
    username="bob",
    password="pass-b1",
)
_REMOTE_B2 = Remote(
    name="remote-b2",
    url="https://b2.cal.example/dav",
    auth_mode="passthrough",
)

_CONFIG_A = Config(
    name="config-a",
    remotes=(_REMOTE_A1, _REMOTE_A2),
    calendars=(
        ("remote-a1", (Calendar(name="cal-a1-1"), Calendar(name="cal-a1-2"))),
        ("remote-a2", (Calendar(name="cal-a2-1"), Calendar(name="cal-a2-2"))),
    ),
)

_CONFIG_B = Config(
    name="config-b",
    remotes=(_REMOTE_B1, _REMOTE_B2),
    calendars=(
        ("remote-b1", (Calendar(name="cal-b1-1"), Calendar(name="cal-b1-2"))),
        ("remote-b2", (Calendar(name="cal-b2-1"), Calendar(name="cal-b2-2"))),
    ),
)

_DB_APP = AppConfig(mode="db", config=None, configs=(_CONFIG_A, _CONFIG_B))

_USER_A = ProUser(username="alice", key_hash="hash-alice", config_names=("config-a",))
_USER_BOTH = ProUser(username="both", key_hash="hash-both", config_names=("config-a", "config-b"))


# ===================================================================
# 1. parse_dotted_path edge cases
# ===================================================================


class TestParseDottedPath:
    """Happy path and error cases for ``parse_dotted_path``."""

    def test_valid_three_parts(self):
        addr = parse_dotted_path("work.nc.team")
        assert addr == CalendarAddress(
            config_name="work",
            remote_name="nc",
            calendar_name="team",
        )

    def test_parts_are_strings(self):
        addr = parse_dotted_path("cfg.rem.cal")
        assert isinstance(addr.config_name, str)
        assert isinstance(addr.remote_name, str)
        assert isinstance(addr.calendar_name, str)

    def test_two_parts_raises(self):
        with pytest.raises(ValueError, match="2 segment"):
            parse_dotted_path("a.b")

    def test_four_parts_raises(self):
        with pytest.raises(ValueError, match="4 segment"):
            parse_dotted_path("a.b.c.d")

    def test_one_part_raises(self):
        with pytest.raises(ValueError, match="1 segment"):
            parse_dotted_path("abc")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="1 segment"):
            parse_dotted_path("")

    def test_empty_middle_part_raises(self):
        with pytest.raises(ValueError, match="segment at position 2"):
            parse_dotted_path("a..b")

    def test_empty_first_part_raises(self):
        with pytest.raises(ValueError, match="segment at position 1"):
            parse_dotted_path(".a.b")

    def test_empty_last_part_raises(self):
        with pytest.raises(ValueError, match="segment at position 3"):
            parse_dotted_path("a.b.")

    def test_whitespace_only_part_raises(self):
        with pytest.raises(ValueError, match="segment at position 2"):
            parse_dotted_path("a. .b")

    def test_tab_only_part_raises(self):
        with pytest.raises(ValueError, match="segment at position 1"):
            parse_dotted_path("\t.b.c")

    def test_leading_trailing_whitespace_in_parts_ok(self):
        # Leading/trailing whitespace within a part is allowed by the
        # structural split — the store charset regex is NOT applied here.
        addr = parse_dotted_path("  cfg  .  rem  .  cal  ")
        assert addr.config_name == "  cfg  "
        assert addr.remote_name == "  rem  "
        assert addr.calendar_name == "  cal  "


# ===================================================================
# 2. resolve_addressed_calendar happy path
# ===================================================================


class TestResolveAddressedCalendarHappyPath:
    """Happy path against the fabricated 2×2×2 AppConfig."""

    def test_resolve_first_config_first_remote_first_cal(self):
        res = resolve_addressed_calendar(_DB_APP, _USER_BOTH, "config-a.remote-a1.cal-a1-1")
        assert res.remote == _REMOTE_A1
        assert res.calendar_name == "cal-a1-1"

    def test_resolve_first_config_second_remote_second_cal(self):
        res = resolve_addressed_calendar(_DB_APP, _USER_BOTH, "config-a.remote-a2.cal-a2-2")
        assert res.remote == _REMOTE_A2
        assert res.calendar_name == "cal-a2-2"

    def test_resolve_second_config_first_remote(self):
        res = resolve_addressed_calendar(_DB_APP, _USER_BOTH, "config-b.remote-b1.cal-b1-1")
        assert res.remote == _REMOTE_B1
        assert res.calendar_name == "cal-b1-1"

    def test_resolve_second_config_passthrough_remote(self):
        res = resolve_addressed_calendar(_DB_APP, _USER_BOTH, "config-b.remote-b2.cal-b2-1")
        assert res.remote == _REMOTE_B2
        assert res.calendar_name == "cal-b2-1"


# ===================================================================
# 3. Unknown config / remote / calendar → identical ValueError
# ===================================================================


class TestResolveAddressedCalendarUnknown:
    """Unknown levels raise the same ValueError (no enumeration)."""

    def test_unknown_config(self):
        with pytest.raises(ValueError, match="unknown calendar path"):
            resolve_addressed_calendar(_DB_APP, _USER_BOTH, "no-such.rem.cal")

    def test_unknown_remote(self):
        with pytest.raises(ValueError, match="unknown calendar path"):
            resolve_addressed_calendar(_DB_APP, _USER_BOTH, "config-a.no-such.cal")

    def test_unknown_calendar(self):
        with pytest.raises(ValueError, match="unknown calendar path"):
            resolve_addressed_calendar(_DB_APP, _USER_BOTH, "config-a.remote-a1.no-such")

    def test_errors_are_same_type_and_message(self):
        """Verify the no-enumeration guarantee: all three errors are identical."""
        errors = []
        for path in ("no-such.rem.cal", "config-a.no-such.cal", "config-a.remote-a1.no-such"):
            with pytest.raises(ValueError, match="unknown calendar path") as exc_info:
                resolve_addressed_calendar(_DB_APP, _USER_BOTH, path)
            errors.append((type(exc_info.value), str(exc_info.value)))
        # All three must be the same type; the message template is identical
        # (different paths produce different *text* but the template is the same)
        for exc_type, _msg in errors:
            assert exc_type is ValueError
        # The template string (sans path) is the same for all — verify via
        # the constant.

        for _exc_type, msg in errors:
            assert msg.startswith("unknown calendar path '")
            assert msg.endswith("'")


# ===================================================================
# 4. Access-denied user gets the same error as not-found
# ===================================================================


class TestResolveAddressedCalendarAccessDenied:
    """Access-denied and not-found must raise identical errors."""

    def test_access_denied_user_a_on_config_b(self):
        """User A has access to config-a only; path into config-b → generic error."""
        path = "config-b.remote-b1.cal-b1-1"
        with pytest.raises(ValueError, match="unknown calendar path") as exc_denied:
            resolve_addressed_calendar(_DB_APP, _USER_A, path)
        # Same path + not-found (config exists but calendar doesn't) → same error
        path_notfound = "config-a.remote-a1.no-such-cal"
        with pytest.raises(ValueError, match="unknown calendar path") as exc_notfound:
            resolve_addressed_calendar(_DB_APP, _USER_A, path_notfound)
        # Same error type (no enumeration — both are generic ValueErrors)
        assert type(exc_denied.value) is type(exc_notfound.value) is ValueError
        # Both messages use the same template (different paths → different text,
        # but the same template string governs both)
        assert exc_denied.value.args[0].startswith("unknown calendar path '")
        assert exc_notfound.value.args[0].startswith("unknown calendar path '")

    def test_access_denied_same_message_as_unknown_config(self):
        """Access-denied path produces the same error type as a non-existent config."""
        with pytest.raises(ValueError, match="unknown calendar path") as exc_denied:
            resolve_addressed_calendar(_DB_APP, _USER_A, "config-b.remote-b1.cal-b1-1")
        with pytest.raises(ValueError, match="unknown calendar path") as exc_unknown:
            resolve_addressed_calendar(_DB_APP, _USER_A, "totally-fake.rem.cal")
        # Same error type (both are ValueErrors — no enumeration)
        assert type(exc_denied.value) is type(exc_unknown.value) is ValueError

    def test_user_a_granted_config_a_resolves(self):
        """User A with a path into config-a resolves successfully."""
        res = resolve_addressed_calendar(_DB_APP, _USER_A, "config-a.remote-a1.cal-a1-1")
        assert res.remote == _REMOTE_A1
        assert res.calendar_name == "cal-a1-1"


# ===================================================================
# 5. user=None skips access filtering
# ===================================================================


class TestResolveAddressedCalendarNoUser:
    """user=None (simple-mode shape) resolves any path without access check."""

    def test_none_user_resolves_config_a(self):
        res = resolve_addressed_calendar(_DB_APP, None, "config-a.remote-a1.cal-a1-1")
        assert res.remote == _REMOTE_A1

    def test_none_user_resolves_config_b(self):
        res = resolve_addressed_calendar(_DB_APP, None, "config-b.remote-b1.cal-b1-1")
        assert res.remote == _REMOTE_B1

    def test_none_user_still_fails_on_genuinely_unknown(self):
        with pytest.raises(ValueError, match="unknown calendar path"):
            resolve_addressed_calendar(_DB_APP, None, "no-such.rem.cal")


# ===================================================================
# 6. Frozen records
# ===================================================================


class TestFrozenRecords:
    """CalendarAddress and Resolution are frozen dataclasses."""

    def test_calendar_address_is_frozen(self):
        addr = CalendarAddress(config_name="a", remote_name="b", calendar_name="c")
        with pytest.raises(AttributeError):
            addr.config_name = "x"  # type: ignore[misc]

    def test_resolution_is_frozen(self):
        res = Resolution(remote=_REMOTE_A1, calendar_name="cal")
        with pytest.raises(AttributeError):
            res.calendar_name = "x"  # type: ignore[misc]

    def test_calendar_address_equality(self):
        a = CalendarAddress("x", "y", "z")
        b = CalendarAddress("x", "y", "z")
        assert a == b

    def test_resolution_equality(self):
        a = Resolution(remote=_REMOTE_A1, calendar_name="cal")
        b = Resolution(remote=_REMOTE_A1, calendar_name="cal")
        assert a == b
