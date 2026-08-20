"""Unit tests for the extracted decorator helper functions."""

import inspect

from caldav_mcp.tools import _filter_public_params, _build_wrapper_annotations


def test_filter_public_params_removes_client():
    def fn(client, cal, uid: str) -> None: ...
    sig = inspect.signature(fn)
    params = _filter_public_params(sig, needs_calendar=True)
    names = [p.name for p in params]
    assert "client" not in names
    assert "cal" not in names
    assert "uid" in names


def test_filter_public_params_keeps_cal_when_no_calendar():
    def fn(client, cal, uid: str) -> None: ...
    sig = inspect.signature(fn)
    params = _filter_public_params(sig, needs_calendar=False)
    names = [p.name for p in params]
    assert "cal" in names
    assert "client" not in names
