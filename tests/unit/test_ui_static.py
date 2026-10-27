"""Unit tests for ``SHOW_UI`` static hosting at ``/ui`` (milestone UI-M3).

Test vehicle (per milestone): Starlette ``TestClient`` against a **fresh**
``FastMCP("ui-test")`` + ``http_app()`` so the shared ``caldav_mcp.mcp``
instance is never polluted.  A fixture dist is built in ``tmp_path`` with a
marker ``index.html``, a content-hashed ``assets/app-1a2b.js``, a nested
``users/alice.html`` marker file, and a weird-extension ``assets/blob.foo`` —
deliberately making ``/ui/users/alice.html`` an *existing nested file*
(served as-is with ``no-cache``) while ``/ui/users/bob`` exercises the SPA
fallback, so the two behaviors never collide (milestone edge-case table).

Covers every row of the edge-case table: fail-fast ``ValueError``, the 404
baseline when routes are never registered, the explicit 307 redirect,
index/fallback vs nested-file serving, asset cache/media types, missing
asset 404 (never HTML-masked), path-traversal rejection, the runtime
dist-deleted edge, and the security/cache header contract.  The ``server.py``
registration gate lives in ``tests/unit/test_config_validation.py``
(``TestShowUIGate``).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastmcp import FastMCP
from starlette.testclient import TestClient

from caldav_mcp import ui_static
from caldav_mcp.ui_static import _sanitize, configure_ui_routes, default_dist_dir

_INDEX_MARKER = "UI-FIXTURE-MARKER"
_ALICE_MARKER = "ALICE-NESTED-MARKER"
_JS_BODY = "console.log('fixture-app');"
_WEIRD_BODY = "weird-extension-bytes"

_CSP = "default-src 'self'"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ui_dist(tmp_path: Path) -> Path:
    """Build the fixture dist described in the module docstring."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "users").mkdir()
    (tmp_path / "index.html").write_text(
        f"<!doctype html><html><head><title>{_INDEX_MARKER}</title></head>"
        '<body><div id="app"></div></body></html>',
        encoding="utf-8",
    )
    (tmp_path / "assets" / "app-1a2b.js").write_text(_JS_BODY, encoding="utf-8")
    (tmp_path / "assets" / "blob.foo").write_text(_WEIRD_BODY, encoding="utf-8")
    (tmp_path / "users" / "alice.html").write_text(f"<p>{_ALICE_MARKER}</p>", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def client(ui_dist: Path):
    """TestClient over a fresh FastMCP instance with the /ui routes registered."""
    mcp = FastMCP("ui-test")
    configure_ui_routes(mcp, dist_dir=ui_dist)
    with TestClient(mcp.http_app()) as test_client:
        yield test_client


def _assert_handler_404(response) -> None:
    """The 404 came from the /ui handler (security headers present), not the framework."""
    assert response.status_code == 404
    assert response.headers.get("content-security-policy") == _CSP
    assert response.headers.get("cache-control") == "no-cache"


def _assert_security_headers(response) -> None:
    assert response.headers.get("content-security-policy") == _CSP
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("referrer-policy") == "no-referrer"


# ---------------------------------------------------------------------------
# Startup fail-fast (milestone decision (c))
# ---------------------------------------------------------------------------


class TestFailFast:
    """``SHOW_UI=true`` with a missing build output refuses to start."""

    def test_value_error_when_index_absent(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError) as excinfo:
            configure_ui_routes(FastMCP("ui-fail-fast"), dist_dir=tmp_path)
        message = str(excinfo.value)
        assert "SHOW_UI is enabled but the UI build output is missing at" in message
        assert "make ui-build" in message
        assert str(tmp_path / "index.html") in message

    def test_value_error_when_dist_dir_absent(self, tmp_path: Path) -> None:
        missing = tmp_path / "no-such-dist"
        with pytest.raises(ValueError, match="UI build output is missing"):
            configure_ui_routes(FastMCP("ui-fail-fast"), dist_dir=missing)

    def test_no_routes_registered_after_failed_configure(self, tmp_path: Path) -> None:
        """The failure happens before any registration — nothing is added."""
        mcp = FastMCP("ui-fail-fast-partial")
        with pytest.raises(ValueError):
            configure_ui_routes(mcp, dist_dir=tmp_path)
        with TestClient(mcp.http_app()) as test_client:
            assert test_client.get("/ui/").status_code == 404


# ---------------------------------------------------------------------------
# 404 baseline when SHOW_UI is off / routes never registered
# ---------------------------------------------------------------------------


class TestRoutesAbsentBaseline:
    """Without configure_ui_routes, ``GET /ui*`` yields the framework 404."""

    def test_framework_404_when_routes_never_registered(self) -> None:
        bare = FastMCP("no-ui-routes")
        with TestClient(bare.http_app()) as test_client:
            for path in ("/ui", "/ui/", "/ui/users/bob", "/ui/assets/app-1a2b.js"):
                response = test_client.get(path)
                assert response.status_code == 404
                # Framework 404, not the /ui handler's (no CSP on it).
                assert "content-security-policy" not in response.headers


# ---------------------------------------------------------------------------
# GET /ui → 307 redirect (explicit route, no query carry-over)
# ---------------------------------------------------------------------------


class TestRootRedirect:
    def test_redirects_to_slash(self, client) -> None:
        response = client.get("/ui", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/ui/"
        _assert_security_headers(response)

    def test_redirect_drops_query_string(self, client) -> None:
        """The redirect target is the literal /ui/ — the SPA never routes on query params."""
        response = client.get("/ui?next=anything", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/ui/"

    def test_follow_redirect_reaches_index(self, client) -> None:
        response = client.get("/ui")
        assert response.status_code == 200
        assert _INDEX_MARKER in response.text


# ---------------------------------------------------------------------------
# Index serving + SPA fallback + existing nested file
# ---------------------------------------------------------------------------


class TestIndexAndFallback:
    def test_root_serves_index_no_cache(self, client) -> None:
        response = client.get("/ui/")
        assert response.status_code == 200
        assert _INDEX_MARKER in response.text
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-cache"
        _assert_security_headers(response)

    def test_deep_link_falls_back_to_index(self, client) -> None:
        """``/ui/users/bob`` does not exist → index.html 200 (SPA fallback)."""
        response = client.get("/ui/users/bob")
        assert response.status_code == 200
        assert _INDEX_MARKER in response.text
        assert response.headers["cache-control"] == "no-cache"

    def test_existing_nested_file_served_not_fallback(self, client) -> None:
        """``/ui/users/alice.html`` ships in the fixture → the actual file, no-cache."""
        response = client.get("/ui/users/alice.html")
        assert response.status_code == 200
        assert _ALICE_MARKER in response.text
        assert _INDEX_MARKER not in response.text
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-cache"
        _assert_security_headers(response)


# ---------------------------------------------------------------------------
# Hashed assets: media type + immutable caching; missing asset 404
# ---------------------------------------------------------------------------


class TestAssets:
    def test_existing_asset_immutable_cache(self, client) -> None:
        response = client.get("/ui/assets/app-1a2b.js")
        assert response.status_code == 200
        assert response.text == _JS_BODY
        assert response.headers["content-type"].startswith("text/javascript")
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
        _assert_security_headers(response)

    def test_missing_asset_404_never_html_masked(self, client) -> None:
        """A stale cached index.html must not mask a missing hashed chunk."""
        response = client.get("/ui/assets/missing.js")
        _assert_handler_404(response)
        assert _INDEX_MARKER not in response.text
        assert not response.headers["content-type"].startswith("text/html")

    def test_unknown_extension_octet_stream(self, client) -> None:
        """``.foo`` on disk → application/octet-stream (explicit map default)."""
        response = client.get("/ui/assets/blob.foo")
        assert response.status_code == 200
        assert response.text == _WEIRD_BODY
        assert response.headers["content-type"].startswith("application/octet-stream")
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


# ---------------------------------------------------------------------------
# Path traversal → 404 (sanitize + resolve containment)
# ---------------------------------------------------------------------------


class TestTraversal:
    @pytest.mark.parametrize(
        "path",
        [
            "/ui/../server.py",  # literal dot segment (clients may normalize)
            "/ui/%2e%2e/server.py",  # percent-encoded dot segment
            "/ui/..%2fserver.py",  # encoded slash variant
            "/ui//etc/passwd",  # absolute path (empty first segment)
            "/ui/..\\server.py",  # backslash form
            "/ui/%5c..%5cetc%5cpasswd",  # encoded backslash form
            "/ui/users/../../server.py",  # dot segment mid-path
        ],
        ids=lambda p: p,
    )
    def test_traversal_rejected_with_404(self, client, path: str) -> None:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 404
        assert _INDEX_MARKER not in response.text

    @pytest.mark.parametrize(
        "path",
        [
            "/ui/%2e%2e/server.py",
            "/ui//etc/passwd",
            "/ui/..\\server.py",
        ],
        ids=lambda p: p,
    )
    def test_traversal_404_comes_from_ui_handler(self, client, path: str) -> None:
        """Percent-encoded/backslash forms reach the handler → its 404 with CSP."""
        response = client.get(path, follow_redirects=False)
        _assert_handler_404(response)

    def test_symlink_escape_containment_404(self, client, ui_dist: Path, tmp_path: Path) -> None:
        """A symlink inside dist pointing outside it fails resolve() containment → 404."""
        outside = tmp_path.parent / "outside-secret.txt"
        outside.write_text("secret", encoding="utf-8")
        link = ui_dist / "users" / "escape.txt"
        try:
            link.symlink_to(outside)
        except OSError:  # pragma: no cover - platform without symlink support
            pytest.skip("symlinks not supported here")
        response = client.get("/ui/users/escape.txt")
        _assert_handler_404(response)
        assert "secret" not in response.text


class TestSanitizeRule:
    """Direct unit coverage of the rejection rule (independent of client normalization)."""

    @pytest.mark.parametrize(
        "asset_path",
        [
            "../server.py",
            "users/../../server.py",
            "assets/./app.js",
            "assets//app.js",
            "/etc/passwd",
            "a\\b",
            "x\x00y",
            "users/",
            "assets/",
            ".",
            "..",
        ],
        ids=lambda p: repr(p),
    )
    def test_rejected(self, asset_path: str) -> None:
        assert _sanitize(asset_path) is None

    @pytest.mark.parametrize(
        ("asset_path", "expected"),
        [
            ("", ""),
            ("index.html", "index.html"),
            ("assets/app-1a2b.js", "assets/app-1a2b.js"),
            ("users/alice.html", "users/alice.html"),
        ],
    )
    def test_accepted(self, asset_path: str, expected: str) -> None:
        assert _sanitize(asset_path) == expected


# ---------------------------------------------------------------------------
# Runtime dist removal (documented tiny edge — startup fail-fast is the guard)
# ---------------------------------------------------------------------------


class TestDistDeletedAtRuntime:
    def test_index_404_after_dist_deleted(self, client, ui_dist: Path) -> None:
        for item in ui_dist.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        response = client.get("/ui/")
        _assert_handler_404(response)

    def test_asset_404_after_dist_deleted(self, client, ui_dist: Path) -> None:
        (ui_dist / "assets" / "app-1a2b.js").unlink()
        response = client.get("/ui/assets/app-1a2b.js")
        _assert_handler_404(response)

    def test_redirect_route_survives(self, client, ui_dist: Path) -> None:
        (ui_dist / "index.html").unlink()
        response = client.get("/ui", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/ui/"


# ---------------------------------------------------------------------------
# Dist path derivation + media-type map
# ---------------------------------------------------------------------------


class TestDistDirAndMediaTypes:
    def test_default_dist_dir_is_repo_ui_dist(self) -> None:
        import caldav_mcp

        expected = Path(caldav_mcp.__file__).resolve().parent.parent / "ui" / "dist"
        assert default_dist_dir() == expected

    @pytest.mark.parametrize(
        ("suffix", "expected_prefix"),
        [
            (".html", "text/html"),
            (".js", "text/javascript"),
            (".mjs", "text/javascript"),
            (".css", "text/css"),
            (".map", "application/json"),
            (".svg", "image/svg+xml"),
            (".png", "image/png"),
            (".ico", "image/x-icon"),
            (".json", "application/json"),
            (".txt", "text/plain"),
            (".woff", "font/woff"),
            (".woff2", "font/woff2"),
            (".unknown-ext", "application/octet-stream"),
        ],
    )
    def test_media_type_map(self, suffix: str, expected_prefix: str) -> None:
        assert ui_static.MEDIA_TYPES.get(suffix, ui_static._DEFAULT_MEDIA_TYPE).startswith(
            expected_prefix
        )

    def test_media_type_lookup_case_insensitive(self) -> None:
        assert ui_static._media_type_for(Path("APP.JS")).startswith("text/javascript")
        assert ui_static._media_type_for(Path("blob.foo")) == "application/octet-stream"
