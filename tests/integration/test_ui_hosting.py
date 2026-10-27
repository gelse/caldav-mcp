"""Admin UI hosting integration tests (milestone UI-M3).

Requires the compose test stack from ``docker-compose.test.yaml`` (the
``make test-integration`` target builds and starts it):

* ``mcp-pro`` (http://localhost:8080) — ``SHOW_UI="true"``: the compose build
  runs the Docker ``ui-builder`` node stage, so the image ships the **real**
  built SPA at ``/app/ui/dist``.  Assertions are name-based: the page title
  is read from the repo's ``ui/index.html`` (proving the served page is the
  real build, not a fixture) and hashed asset URLs are discovered from the
  served ``index.html`` itself — no hardcoded hashed filenames.
* ``mcp-simple`` (http://localhost:8081) — ``SHOW_UI`` unset (default
  ``false``): ``/ui*`` must not exist (framework 404), pinning the
  hosting-off-by-default contract; plus the admin-API-disabled contract the
  SPA relies on (404 with a **non-JSON** body → the UI renders its
  "admin API not available" screen, never "wrong key").
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import requests

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_PRO_URL = os.environ.get("MCP_PRO_URL", "http://localhost:8080")
_SIMPLE_URL = os.environ.get("MCP_SIMPLE_URL", "http://localhost:8081")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Asset references inside the served index.html (Vite emits /ui/assets/…
# because the SPA is built with base "/ui/").
_ASSET_RE = re.compile(r'(/ui/assets/[^"\']+)')
_TITLE_RE = re.compile(r"<title>([^<]+)</title>")

_CSP = "default-src 'self'"


def _source_title() -> str:
    """``<title>`` of the repo's ``ui/index.html`` — the name-based ground truth."""
    html = (_PROJECT_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    match = _TITLE_RE.search(html)
    assert match, "ui/index.html must ship a <title> element"
    return match.group(1)


def _title_of(body: str) -> str:
    match = _TITLE_RE.search(body)
    assert match, "served page must ship a <title> element"
    return match.group(1)


class TestUIServingOnPro:
    """``mcp-pro`` runs with ``SHOW_UI=true`` — the Docker-built SPA is served."""

    def test_root_redirects_to_slash(self) -> None:
        response = requests.get(f"{_PRO_URL}/ui", allow_redirects=False, timeout=10)
        assert response.status_code == 307
        assert response.headers["location"] == "/ui/"

    def test_index_serves_real_built_spa(self) -> None:
        response = requests.get(f"{_PRO_URL}/ui/", timeout=10)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        # Proves the real build (title matches ui/index.html), not a fixture.
        assert _title_of(response.text) == _source_title()
        # Header policy from the milestone: no-cache + security headers.
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers.get("content-security-policy") == _CSP
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("referrer-policy") == "no-referrer"

    def test_deep_route_falls_back_to_same_index(self) -> None:
        """Unmatched deep link → 200 with the identical index.html body."""
        index = requests.get(f"{_PRO_URL}/ui/", timeout=10)
        deep = requests.get(f"{_PRO_URL}/ui/some/deep/route", timeout=10)
        assert index.status_code == 200
        assert deep.status_code == 200
        assert deep.text == index.text
        assert deep.headers["cache-control"] == "no-cache"

    def test_missing_asset_is_404_not_html(self) -> None:
        """A stale cached index referencing an old chunk must surface as an error."""
        response = requests.get(f"{_PRO_URL}/ui/assets/definitely-missing.js", timeout=10)
        assert response.status_code == 404
        assert _source_title() not in response.text
        assert not response.headers["content-type"].startswith("text/html")

    def test_discovered_asset_cached_immutable(self) -> None:
        """Fetch a hashed asset *discovered* from index.html (no name coupling)."""
        index = requests.get(f"{_PRO_URL}/ui/", timeout=10)
        assets = _ASSET_RE.findall(index.text)
        assert assets, "the built index.html must reference /ui/assets/… files"
        asset_path = assets[0]
        response = requests.get(f"{_PRO_URL}{asset_path}", timeout=10)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert response.headers.get("x-content-type-options") == "nosniff"

    def test_mcp_endpoint_unaffected(self) -> None:
        """The /ui routes never shadow /mcp (framework 401/400-class, not 404-by-UI)."""
        response = requests.post(f"{_PRO_URL}/mcp", json={}, timeout=10)
        assert response.status_code in (400, 401, 406)


class TestUIHostingDisabled:
    """``mcp-simple`` keeps the default ``SHOW_UI=false`` — no /ui routes exist."""

    def test_index_is_framework_404(self) -> None:
        response = requests.get(f"{_SIMPLE_URL}/ui/", timeout=10)
        assert response.status_code == 404

    def test_root_is_framework_404(self) -> None:
        response = requests.get(f"{_SIMPLE_URL}/ui", allow_redirects=False, timeout=10)
        assert response.status_code == 404

    def test_deep_route_is_framework_404(self) -> None:
        response = requests.get(f"{_SIMPLE_URL}/ui/users/bob", timeout=10)
        assert response.status_code == 404


class TestAdminApiDisabledContract:
    """Unregistered ``/admin/*`` → 404 with a **non-JSON** body (decision (d)).

    The SPA's fetch layer uses this to render its "admin API not available"
    screen instead of "wrong key".  Pinned here with no server change.
    """

    def test_admin_users_404_without_json_body(self) -> None:
        response = requests.get(f"{_SIMPLE_URL}/admin/users", timeout=10)
        assert response.status_code == 404
        assert "json" not in response.headers.get("content-type", "")
        with pytest.raises(ValueError):
            response.json()

    def test_admin_configs_404_without_json_body(self) -> None:
        response = requests.get(f"{_SIMPLE_URL}/admin/configs", timeout=10)
        assert response.status_code == 404
        assert "json" not in response.headers.get("content-type", "")
        with pytest.raises(ValueError):
            response.json()
