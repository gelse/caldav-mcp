"""Static hosting for the admin SPA at ``/ui`` (``SHOW_UI=true``).

Handler-based serving on the existing FastMCP/Starlette listener: fastmcp's
``custom_route`` hard-codes ``starlette.routing.Route``
(``fastmcp/server/mixins/transport.py``), so ``StaticFiles``/``Mount``
registration is impossible and both routes are plain request handlers
(milestone UI-M3 design (b)).

Registration contract — identical to ``configure_admin_routes``: the routes
must be added to the FastMCP instance **before** ``run_http_async`` (which
builds the Starlette app internally); fastmcp merges custom routes with
lowest precedence, so ``/ui`` can never shadow ``/mcp`` or ``/admin/*``.

Behavior (milestone UI-M3 edge-case table):

* ``GET /ui`` → explicit ``307`` redirect to the literal ``/ui/`` (no query
  handling — the SPA has no router and never uses query params).
* ``GET /ui/…`` → the matching file when it exists; a missing hashed asset
  under ``assets/`` → ``404`` (a stale cached ``index.html`` must never be
  HTML-masked over a missing chunk); any other unmatched path →
  ``index.html`` with ``200`` (SPA fallback for deep links).
* Path traversal (``.``, ``..``, empty segments, absolute paths,
  backslashes, NUL) → ``404``, belt-and-braces with a ``Path.resolve()``
  containment check against the dist directory.
* Fail-fast at startup: when ``index.html`` is absent,
  :func:`configure_ui_routes` raises ``ValueError`` with an actionable
  message — ``SHOW_UI=true`` is explicit deployment intent, so refusing to
  start beats serving a broken page (decision (c)).
* Every response carries ``X-Content-Type-Options: nosniff``,
  ``Referrer-Policy: no-referrer``, and CSP ``default-src 'self'`` — safe
  because UI-M2 enforces CSP-by-construction (no inline script/style in the
  built ``index.html``).  Cache policy: immutable year-long caching for
  content-hashed ``assets/`` files, ``no-cache`` everywhere else.

Media types come from an explicit extension map (not the OS ``mimetypes``
module): Alpine images lack ``/etc/mime.types``, and a deterministic map
behaves identically on dev machines and in the runtime container.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote

from starlette.requests import Request
from starlette.responses import FileResponse, RedirectResponse, Response

if TYPE_CHECKING:
    from fastmcp import FastMCP

# Explicit extension → media type map (deterministic across environments;
# unknown extensions fall back to application/octet-stream).
MEDIA_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

_DEFAULT_MEDIA_TYPE = "application/octet-stream"

# Security headers on every /ui response (CSP viable because UI-M2's
# post-build gate proves dist/index.html has no inline script/style).
_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'",
}

# Vite content-hashes filenames under assets/ → safe to cache forever.
_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
_NO_CACHE = "no-cache"


def default_dist_dir() -> Path:
    """Return the SPA build-output directory (no env var — one parameter).

    ``<repo>/ui/dist`` locally (this module lives in ``<repo>/caldav_mcp``);
    ``/app/ui/dist`` in the Docker image, where ``caldav_mcp`` sits at
    ``/app/caldav_mcp`` and the runtime stage copies the built ``ui/dist``
    next to it.
    """
    return Path(__file__).resolve().parent.parent / "ui" / "dist"


def _sanitize(asset_path: str) -> str | None:
    """Return a safe dist-relative path, or ``None`` when the request must 404.

    Rejects empty segments (``//`` or a trailing slash), ``.``/``..``
    segments, absolute paths, backslashes, and NUL.  The containment check
    against the dist directory (symlink escape) happens in the caller after
    ``Path.resolve()`` — this function only normalizes the literal input.
    """
    if not asset_path:
        return ""  # /ui/ → index fallback (step 4 of the handler)
    if "\x00" in asset_path or "\\" in asset_path:
        return None
    if asset_path.startswith("/"):
        return None  # absolute path (e.g. /ui//etc/passwd)
    segments = asset_path.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        return None
    return asset_path


def _media_type_for(path: Path) -> str:
    """Return the media type for *path* from the explicit map."""
    return MEDIA_TYPES.get(path.suffix.lower(), _DEFAULT_MEDIA_TYPE)


def _headers(cache_control: str) -> dict[str, str]:
    """Response headers: security headers plus the cache policy."""
    return {**_SECURITY_HEADERS, "Cache-Control": cache_control}


def _not_found() -> Response:
    """Plain-text 404 carrying the security headers (never HTML-masked)."""
    return Response(
        "Not Found",
        status_code=404,
        media_type="text/plain; charset=utf-8",
        headers=_headers(_NO_CACHE),
    )


def _serve_index(dist: Path) -> Response:
    """Serve ``index.html`` (SPA fallback), or 404 when it is gone at runtime."""
    index = dist / "index.html"
    if index.is_file():
        return FileResponse(index, media_type=_media_type_for(index), headers=_headers(_NO_CACHE))
    # dist deleted under a running server — the startup fail-fast is the
    # real guard; this documented edge returns 404 instead of a broken page.
    return _not_found()


def _serve_asset(dist: Path, asset_path: str) -> Response:
    """Serve a sanitized asset path per the milestone serving rules."""
    candidate = dist / asset_path
    try:
        resolved = candidate.resolve()
        contained = resolved.is_relative_to(dist.resolve())
    except OSError:
        return _not_found()
    if not contained:
        # Containment failure (symlink escape) → 404, same as sanitize hits.
        return _not_found()
    if resolved.is_file():
        cache = _ASSET_CACHE_CONTROL if asset_path.startswith("assets/") else _NO_CACHE
        return FileResponse(resolved, media_type=_media_type_for(resolved), headers=_headers(cache))
    if asset_path.startswith("assets/"):
        # A missing hashed asset surfaces as an error: a stale cached
        # index.html referencing an old chunk is a deploy mismatch.
        return _not_found()
    return _serve_index(dist)


async def _handle_ui_root(request: Request) -> Response:
    """``GET /ui`` → 307 to the literal ``/ui/`` (explicit, no query carry-over)."""
    return RedirectResponse("/ui/", status_code=307, headers=_SECURITY_HEADERS)


def _make_ui_asset_handler(dist: Path) -> Callable[[Request], Awaitable[Response]]:
    """Build the ``/ui/{asset_path:path}`` handler bound to *dist*."""

    async def _handle_ui_asset(request: Request) -> Response:
        # ``unquote`` again so the traversal rules hold even when the ASGI
        # layer hands the path over still percent-encoded (idempotent for
        # normal names; the fixture dist never ships a literal %XX name).
        raw = unquote(request.path_params.get("asset_path", ""))
        sanitized = _sanitize(raw)
        if sanitized is None:
            return _not_found()
        if sanitized:
            return _serve_asset(dist, sanitized)
        return _serve_index(dist)  # /ui/ → index.html (step 4)

    return _handle_ui_asset


def configure_ui_routes(mcp: FastMCP, dist_dir: Path | None = None) -> None:
    """Register the ``/ui`` static-hosting routes on *mcp* (``server.py``).

    Fails fast with an actionable ``ValueError`` when ``dist/index.html`` is
    absent — ``SHOW_UI=true`` is explicit deployment intent (milestone
    decision (c)).  Must be called before ``run_http_async``; fastmcp merges
    custom routes with lowest precedence so ``/mcp`` and ``/admin/*`` are
    never shadowed.
    """
    dist = Path(dist_dir) if dist_dir is not None else default_dist_dir()
    index = dist / "index.html"
    if not index.is_file():
        raise ValueError(
            f"SHOW_UI is enabled but the UI build output is missing at {index} — "
            "run 'make ui-build' or build the Docker image (which builds ui/ automatically)"
        )
    # Explicit /ui → /ui/ redirect (deterministic, directly testable — not
    # reliant on Starlette's redirect_slashes interplay with merged routes).
    mcp.custom_route("/ui", methods=["GET"], include_in_schema=False)(_handle_ui_root)
    mcp.custom_route("/ui/{asset_path:path}", methods=["GET"], include_in_schema=False)(
        _make_ui_asset_handler(dist)
    )
