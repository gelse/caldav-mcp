"""Exercise python-caldav's iCloud UID fallback through the MCP tools."""

from types import SimpleNamespace
from unittest import mock
from urllib.parse import unquote

import pytest
from caldav import Calendar, DAVClient, Event
from caldav.lib.error import ReportError
from conftest import make_event, patch_caldav

import server
from caldav_mcp.errors import Status

UID = "event-123@example.com"


@pytest.mark.parametrize("tool", ["caldav_get_event_by_uid", "caldav_list_attendees"])
@pytest.mark.parametrize("direct_get", [True, False])
def test_uid_lookup_after_report_412(tool, direct_get):
    client = DAVClient(url="https://example.com/")
    calendar = Calendar(client=client, url="https://example.com/calendar/")
    data = make_event(uid=UID, attendees=["alice@example.com"]).to_ical().decode()
    event = Event(client=client, parent=calendar, data=data)
    response = SimpleNamespace(status=200 if direct_get else 404, raw=data, headers={})

    def search(**kwargs):
        if "uid" in kwargs:
            raise ReportError(status=412, reason="Precondition Failed")
        return [event]

    patchers = patch_caldav(calendar)
    try:
        with mock.patch.object(calendar, "search", side_effect=search) as search_mock:
            with mock.patch.object(client, "request", return_value=response) as request_mock:
                result = getattr(server, tool)(uid=UID)
        assert result.status == Status.OK
        expected = UID if tool == "caldav_get_event_by_uid" else "alice@example.com"
        assert expected in result.message
        request_mock.assert_called_once()
        assert unquote(request_mock.call_args.args[0]).endswith(f"/{UID}.ics")
        assert search_mock.call_count == (1 if direct_get else 2)
    finally:
        for patcher in patchers:
            patcher.stop()
