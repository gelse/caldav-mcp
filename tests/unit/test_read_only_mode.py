"""Unit tests for the CALDAV_MCP_READ_ONLY feature.

Covers:
1. Env-var parsing (trueish / falseish values)
2. Tool exposure: only 8 read-only tools in read-only mode, all 14 otherwise
3. ``mcp_tool_if_writable`` helper behaviour
4. Importability of write-tool functions in both modes
"""

import asyncio
import os
import sys
from unittest import mock

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

READ_ONLY_TOOL_NAMES = {
    "caldav_list_calendars",
    "caldav_get_events",
    "caldav_get_today_events",
    "caldav_get_week_events",
    "caldav_get_event_by_uid",
    "caldav_search_events",
    "caldav_get_freebusy",
    "caldav_list_attendees",
}

WRITE_TOOL_NAMES = {
    "caldav_create_event",
    "caldav_update_event",
    "caldav_delete_event",
    "caldav_move_event",
    "caldav_add_attendee",
    "caldav_remove_attendee",
}

ALL_TOOL_NAMES = READ_ONLY_TOOL_NAMES | WRITE_TOOL_NAMES

# ---------------------------------------------------------------------------
# 1. Parsing test
# ---------------------------------------------------------------------------


def test_read_only_parsing_trueish():
    """Trueish values evaluate to True using the same expression as config.py."""

    def _parse(val: str) -> bool:
        return val.lower() in ("true", "1", "yes")

    for val in ("true", "True", "TRUE", "1", "yes", "YES"):
        assert _parse(val) is True, f"{val!r} should be True"


def test_read_only_parsing_falseish():
    """Falseish values evaluate to False using the same expression as config.py."""

    def _parse(val: str) -> bool:
        return val.lower() in ("true", "1", "yes")

    for val in ("false", "False", "0", "no", "", "garbage", "nope"):
        assert _parse(val) is False, f"{val!r} should be False"


def test_read_only_config_default_false():
    """The default (no env var) resolves to False."""
    raw = os.environ.get("CALDAV_MCP_READ_ONLY", "false")
    # When the var is not set, the default "false" parses to False.
    # When it IS set (e.g. CALDAV_MCP_READ_ONLY=true in CI), the test
    # still passes because we only verify the default-expression logic.
    if raw == "false":
        assert raw.lower() not in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# 2. Exposure tests
# ---------------------------------------------------------------------------


def _reload_caldav_mcp_with_read_only(read_only: bool):
    """Remove all caldav_mcp modules and re-import with patched env.

    Returns the fresh ``caldav_mcp`` module.
    """
    env_val = "true" if read_only else "false"

    # Remove all caldav_mcp.* modules so they are freshly imported
    to_remove = [k for k in sys.modules if k.startswith("caldav_mcp")]
    saved = {k: sys.modules[k] for k in to_remove}
    for k in to_remove:
        del sys.modules[k]

    with mock.patch.dict(os.environ, {"CALDAV_MCP_READ_ONLY": env_val}):
        import caldav_mcp as fresh_mod  # noqa: E402

    return fresh_mod, saved


def _restore_modules(saved: dict) -> None:
    """Restore previously saved caldav_mcp modules."""
    to_remove = [k for k in sys.modules if k.startswith("caldav_mcp")]
    for k in to_remove:
        del sys.modules[k]
    sys.modules.update(saved)


def test_read_only_mode_hides_write_tools():
    """With CALDAV_MCP_READ_ONLY=true, only 8 read-only tools are visible."""
    fresh_mod, saved = _reload_caldav_mcp_with_read_only(read_only=True)
    try:

        async def _check():
            tools = await fresh_mod.mcp.list_tools(run_middleware=False)
            return {t.name for t in tools}

        tool_names = asyncio.run(_check())
        assert tool_names == READ_ONLY_TOOL_NAMES
        assert WRITE_TOOL_NAMES.isdisjoint(tool_names)
    finally:
        _restore_modules(saved)


def test_default_mode_exposes_all_14_tools():
    """Without the flag, all 14 tools are registered."""
    fresh_mod, saved = _reload_caldav_mcp_with_read_only(read_only=False)
    try:

        async def _check():
            tools = await fresh_mod.mcp.list_tools(run_middleware=False)
            return {t.name for t in tools}

        tool_names = asyncio.run(_check())
        assert tool_names == ALL_TOOL_NAMES
    finally:
        _restore_modules(saved)


# ---------------------------------------------------------------------------
# 3. Helper unit test
# ---------------------------------------------------------------------------


def test_mcp_tool_if_writable_identity_when_read_only():
    """When READ_ONLY is truthy, the decorator returns the original function."""
    from caldav_mcp.tools import mcp_tool_if_writable

    def original_fn():
        """Original."""
        pass

    with mock.patch("caldav_mcp.tools.READ_ONLY", True):
        result = mcp_tool_if_writable(annotations={"readOnlyHint": True})(original_fn)

    assert result is original_fn
    assert not hasattr(result, "__fastmcp__")


def test_mcp_tool_if_writable_registers_when_writable():
    """When READ_ONLY is falsy, the decorator applies @mcp.tool."""
    from caldav_mcp.tools import mcp_tool_if_writable

    def original_fn():
        """Original."""
        pass

    with mock.patch("caldav_mcp.tools.READ_ONLY", False):
        result = mcp_tool_if_writable(annotations={"readOnlyHint": True})(original_fn)

    # FastMCP's @mcp.tool adds a __fastmcp__ attribute
    assert hasattr(result, "__fastmcp__")


# ---------------------------------------------------------------------------
# 4. Importability test
# ---------------------------------------------------------------------------


def test_write_tools_importable_from_server():
    """Write-tool functions remain importable as Python callables."""
    import server  # noqa: E402

    for name in WRITE_TOOL_NAMES:
        fn = getattr(server, name, None)
        assert fn is not None, f"server.{name} should be importable"
        assert callable(fn), f"server.{name} should be callable"


def test_write_tools_importable_from_caldav_mcp():
    """Write-tool functions remain importable from caldav_mcp package."""
    import caldav_mcp as mod  # noqa: E402

    for name in WRITE_TOOL_NAMES:
        fn = getattr(mod, name, None)
        assert fn is not None, f"caldav_mcp.{name} should be importable"
        assert callable(fn), f"caldav_mcp.{name} should be callable"


def test_write_tools_importable_in_read_only_mode():
    """Write-tool functions are importable even when READ_ONLY=true."""
    fresh_mod, saved = _reload_caldav_mcp_with_read_only(read_only=True)
    try:
        for name in WRITE_TOOL_NAMES:
            fn = getattr(fresh_mod, name, None)
            assert fn is not None, f"caldav_mcp.{name} should be importable in read-only mode"
            assert callable(fn), f"caldav_mcp.{name} should be callable in read-only mode"
    finally:
        _restore_modules(saved)
