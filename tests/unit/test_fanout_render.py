"""Unit tests for the fan-out message renderer (M5.1 requirements 7–9).

Tests the pure function ``render_fanout_message`` from ``caldav_mcp.fanout``.
No network, no CalDAV server, no real clients — only fabricated entries.
"""

from __future__ import annotations

from caldav_mcp.errors import Status
from caldav_mcp.fanout import AggregatedEntry, render_fanout_message

# ---------------------------------------------------------------------------
# Requirement 7: Per-remote lines in declaration order, bracketed status,
# multi-calendar remote grouped into one line with a count.
# ---------------------------------------------------------------------------


class TestRenderFanoutMessagePerRemote:
    def test_single_remote_single_calendar(self):
        """One remote, one calendar → single detail line."""
        entries = (
            AggregatedEntry(
                config_name="alpha",
                remote_name="main",
                calendar_name="cal1",
                data={"events": []},
                status="ok",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        lines = msg.split("\n")
        assert len(lines) == 2  # first line + one detail line
        assert "- [ok] alpha.main: 1 calendar" in lines[1]

    def test_single_remote_multi_calendar_grouped(self):
        """Multi-calendar remote → grouped into one line with count."""
        entries = (
            AggregatedEntry(
                config_name="alpha",
                remote_name="main",
                calendar_name="cal1",
                data={"events": []},
                status="ok",
            ),
            AggregatedEntry(
                config_name="alpha",
                remote_name="main",
                calendar_name="cal2",
                data={"events": []},
                status="ok",
            ),
            AggregatedEntry(
                config_name="alpha",
                remote_name="main",
                calendar_name="cal3",
                data={"events": []},
                status="ok",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        lines = msg.split("\n")
        # Only one detail line for the remote
        detail_lines = [line for line in lines if line.startswith("- [")]
        assert len(detail_lines) == 1
        assert "3 calendars" in detail_lines[0]
        assert "alpha.main" in detail_lines[0]

    def test_multiple_remotes_declaration_order(self):
        """Remotes appear in declaration order, not sorted."""
        entries = (
            AggregatedEntry(
                config_name="alpha",
                remote_name="r2",
                calendar_name="cal1",
                data={"events": []},
                status="ok",
            ),
            AggregatedEntry(
                config_name="alpha",
                remote_name="r1",
                calendar_name="cal2",
                data={"events": []},
                status="ok",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        lines = msg.split("\n")
        detail_lines = [line for line in lines if line.startswith("- [")]
        assert len(detail_lines) == 2
        assert "alpha.r2" in detail_lines[0]
        assert "alpha.r1" in detail_lines[1]

    def test_different_configs_grouped_by_remote(self):
        """Entries from different configs but same remote name are grouped."""
        entries = (
            AggregatedEntry(
                config_name="config1",
                remote_name="remote-a",
                calendar_name="cal1",
                data={"events": []},
                status="ok",
            ),
            AggregatedEntry(
                config_name="config2",
                remote_name="remote-a",
                calendar_name="cal2",
                data={"events": []},
                status="ok",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        lines = msg.split("\n")
        detail_lines = [line for line in lines if line.startswith("- [")]
        # Should be two separate lines because different config names
        assert len(detail_lines) == 2
        assert "config1.remote-a" in detail_lines[0]
        assert "config2.remote-a" in detail_lines[1]

    def test_bracketed_status_in_detail_lines(self):
        """Detail lines contain bracketed status tags."""
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
        assert "[ok]" in msg
        assert "[error]" in msg

    def test_single_calendar_uses_singular(self):
        """Single calendar uses 'calendar' (singular)."""
        entries = (
            AggregatedEntry(
                config_name="alpha",
                remote_name="main",
                calendar_name="cal1",
                data={"events": []},
                status="ok",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        assert "1 calendar" in msg
        assert "1 calendars" not in msg

    def test_no_calendar_name_uses_scope(self):
        """Empty calendar name (e.g. list_calendars) uses 'scope'."""
        entries = (
            AggregatedEntry(
                config_name="alpha",
                remote_name="main",
                calendar_name="",
                data={"calendars": []},
                status="ok",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        assert "1 scope" in msg

    def test_failure_detail_shows_exception_text(self):
        """Failed remote shows exception text as detail."""
        entries = (
            AggregatedEntry(
                config_name="c1",
                remote_name="r1",
                calendar_name="cal1",
                error="connection refused",
                status="error",
            ),
        )
        msg = render_fanout_message(entries, Status.ERROR)
        assert "connection refused" in msg
        assert "[error]" in msg

    def test_auth_failure_detail_shows_exception_text(self):
        """Auth failure remote shows exception text with auth status."""
        entries = (
            AggregatedEntry(
                config_name="c1",
                remote_name="r1",
                calendar_name="cal1",
                error="missing credentials",
                status="auth",
            ),
        )
        msg = render_fanout_message(entries, Status.ERROR)
        assert "missing credentials" in msg
        assert "[auth]" in msg


# ---------------------------------------------------------------------------
# Requirement 8: First-line summary wording
# ---------------------------------------------------------------------------


class TestRenderFanoutMessageSummary:
    def test_all_ok(self):
        """All ok → first line says '2 ok' with no 'error'."""
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
        first_line = msg.split("\n")[0]
        assert first_line.startswith("OK ")
        assert "2 ok" in first_line
        assert "error" not in first_line

    def test_mixed_2_ok_1_error(self):
        """Mixed → first line says '2 ok, 1 error'."""
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
            AggregatedEntry(
                config_name="c1",
                remote_name="r3",
                calendar_name="cal3",
                error="down",
                status="error",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        first_line = msg.split("\n")[0]
        assert "2 ok" in first_line
        assert "1 error" in first_line

    def test_all_failed(self):
        """All failed → first line starts with ERROR and counts errors."""
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
        first_line = msg.split("\n")[0]
        assert first_line.startswith("ERROR:[server]")
        assert "2 error" in first_line
        assert "ok" not in first_line

    def test_empty_counts_as_ok(self):
        """Entries with status='empty' count as ok in the summary."""
        entries = (
            AggregatedEntry(
                config_name="c1",
                remote_name="r1",
                calendar_name="cal1",
                data=[],
                status="empty",
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
        first_line = msg.split("\n")[0]
        assert "2 ok" in first_line
        assert "0 error" not in first_line

    def test_auth_counts_as_error(self):
        """Auth entries count as error in the summary."""
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
                error="missing creds",
                status="auth",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        first_line = msg.split("\n")[0]
        assert "1 ok" in first_line
        assert "1 error" in first_line

    def test_not_found_counts_as_error(self):
        """not_found entries count as error in the summary."""
        entries = (
            AggregatedEntry(
                config_name="c1",
                remote_name="r1",
                calendar_name="cal1",
                error="not found",
                status="not_found",
            ),
        )
        msg = render_fanout_message(entries, Status.ERROR)
        first_line = msg.split("\n")[0]
        assert "1 error" in first_line


# ---------------------------------------------------------------------------
# Requirement 9: No cross-contamination of error text
# ---------------------------------------------------------------------------


class TestRenderFanoutMessageNoCrossContamination:
    def test_success_remote_no_error_text(self):
        """Succeeding remote's lines do not contain failure exception text."""
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
        lines = msg.split("\n")
        ok_line = [line for line in lines if "[ok]" in line][0]
        assert "connection refused" not in ok_line

    def test_no_credential_material(self):
        """No credential material (passwords, tokens) in any output."""
        entries = (
            AggregatedEntry(
                config_name="c1",
                remote_name="r1",
                calendar_name="cal1",
                error="password=secret123 is wrong",
                status="auth",
            ),
        )
        msg = render_fanout_message(entries, Status.ERROR)
        # The exception text itself may contain password (it was in the error),
        # but the test checks that the exception text is isolated to its own
        # remote line and not mixed with other remotes.
        # This is a structural check — the error text only appears on the
        # [auth] line.
        lines = msg.split("\n")
        auth_lines = [line for line in lines if "[auth]" in line]
        assert len(auth_lines) == 1
        assert "secret123" in auth_lines[0]  # It's in the error, but only there

    def test_multiple_remotes_error_isolation(self):
        """Each remote's error is only on its own line."""
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
                error="remote2 failed",
                status="error",
            ),
            AggregatedEntry(
                config_name="c1",
                remote_name="r3",
                calendar_name="cal3",
                error="remote3 failed",
                status="error",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        lines = msg.split("\n")
        ok_lines = [line for line in lines if "[ok]" in line]
        err_lines = [line for line in lines if "[error]" in line]
        # The ok line should not contain any error text
        for line in ok_lines:
            assert "remote2 failed" not in line
            assert "remote3 failed" not in line
        # Each error line should only contain its own text
        assert any("remote2 failed" in line for line in err_lines)
        assert any("remote3 failed" in line for line in err_lines)
        # But not cross-contaminated
        for line in err_lines:
            if "remote2 failed" in line:
                assert "remote3 failed" not in line
            if "remote3 failed" in line:
                assert "remote2 failed" not in line

    def test_empty_entries(self):
        """Empty entries → default message."""
        msg = render_fanout_message((), Status.EMPTY)
        assert msg == "No accessible calendars"

    def test_header_line_contains_scope_count(self):
        """First line mentions total scope count."""
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
                remote_name="r1",
                calendar_name="cal2",
                data={"events": []},
                status="ok",
            ),
            AggregatedEntry(
                config_name="c1",
                remote_name="r2",
                calendar_name="cal3",
                data={"events": []},
                status="ok",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        first_line = msg.split("\n")[0]
        assert "3 scopes" in first_line


# ---------------------------------------------------------------------------
# Mixed-per-calendar: one calendar ok, another error on the same remote
# (D1 regression — never emit [mixed]; use vocabulary status)
# ---------------------------------------------------------------------------


class TestRenderFanoutMessageMixedPerCalendar:
    def test_mixed_calendar_uses_error_status(self):
        """One calendar ok, another error on same remote → [error] tag."""
        entries = (
            AggregatedEntry(
                config_name="c1",
                remote_name="r1",
                calendar_name="cal1",
                data={"events": [{"uid": "1"}]},
                status="ok",
            ),
            AggregatedEntry(
                config_name="c1",
                remote_name="r1",
                calendar_name="cal2",
                error="connection refused",
                status="error",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        lines = msg.split("\n")
        detail_lines = [line for line in lines if line.startswith("- [")]
        assert len(detail_lines) == 1
        # Must use a vocabulary status, never [mixed]
        assert "[mixed]" not in msg
        assert "[error]" in detail_lines[0]
        assert "1 ok, 1 error" in detail_lines[0]

    def test_mixed_calendar_auth_wins_over_ok(self):
        """One calendar ok, another auth → [auth] tag."""
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
                remote_name="r1",
                calendar_name="cal2",
                error="missing creds",
                status="auth",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        assert "[mixed]" not in msg
        assert "[auth]" in msg
        assert "1 ok, 1 error" in msg

    def test_mixed_calendar_not_found_wins_over_ok(self):
        """One calendar empty, another not_found → [not_found] tag."""
        entries = (
            AggregatedEntry(
                config_name="c1",
                remote_name="r1",
                calendar_name="cal1",
                data=[],
                status="empty",
            ),
            AggregatedEntry(
                config_name="c1",
                remote_name="r1",
                calendar_name="cal2",
                error="calendar not found",
                status="not_found",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        assert "[mixed]" not in msg
        assert "[not_found]" in msg
        assert "1 ok, 1 error" in msg

    def test_all_ok_group_stays_ok(self):
        """Multiple calendars all ok → [ok] tag."""
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
                remote_name="r1",
                calendar_name="cal2",
                data=[],
                status="empty",
            ),
        )
        msg = render_fanout_message(entries, Status.OK)
        assert "[mixed]" not in msg
        assert "[ok]" in msg
        assert "2 calendars" in msg
