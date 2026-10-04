"""Serves the built dashboard (``frontend/dist``) from the hub itself.

One origin for the page, the API, the WebSocket and the streams: no CORS, and stream tickets
work unchanged. Unknown paths fall back to ``index.html`` so client-side routes survive a reload;
paths with a file extension never do, so a missing script is a 404, not an HTML page.

Files under ``assets/`` have content hashes in their names and are cached for a year; everything
else is revalidated. Pre-compressed copies (``.br``/``.gz``, written at build time) are sent to
browsers that accept them, so nothing is compressed per request and streaming responses are left
alone.
"""

import mimetypes
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from vision_hub.core.logging import get_logger

logger = get_logger(__name__)

# The dashboard loads only its own files and talks only to this hub. No inline scripts or
# styles (the build emits none), no plugins, no framing.
PAGE_CSP = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "manifest-src 'self'",
        "worker-src 'self'",
        "media-src 'none'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
# Top-level names that belong to the API or to FastAPI, never to the dashboard.
RESERVED = frozenset({"api", "docs", "redoc", "openapi.json", "metrics"})
ENCODINGS = (("br", ".br"), ("gzip", ".gz"))
SERVICE_WORKER = "sw.js"

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")


def install_dashboard(app: FastAPI, directory: Path) -> bool:
    """Adds the dashboard routes; returns False (and logs why) if there is nothing to serve.
    Must run after every other route is registered: it catches all remaining GET paths."""
    root = directory.resolve()
    index = root / "index.html"
    if not index.is_file():
        logger.warning(
            "dashboard_not_found", path=str(root), hint="run `npm run build` in frontend/"
        )
        return False

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def dashboard(path: str, request: Request) -> Response:
        if path.split("/", 1)[0] in RESERVED:
            raise StarletteHTTPException(404)
        file = _resolve(root, path)
        if file is None:
            if Path(path).suffix:  # a missing file, not a page
                raise StarletteHTTPException(404)
            file = index
        return _send(file, root, request.headers.get("accept-encoding", ""))

    logger.info("dashboard_enabled", path=str(root))
    return True


def _resolve(root: Path, path: str) -> Path | None:
    """The file inside ``root`` that ``path`` names, if any. Paths that resolve outside it
    (``..``, absolute paths, symlinks out) are treated as missing."""
    if not path:
        return None
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _send(file: Path, root: Path, accept_encoding: str) -> FileResponse:
    media_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    hashed = file.parent == root / "assets"
    headers = {"Cache-Control": IMMUTABLE if hashed else REVALIDATE, "Vary": "Accept-Encoding"}
    # Pages and the service worker get the page policy: a worker obeys the CSP sent with its own
    # script, and the API's `default-src 'none'` would forbid it to fetch (and cache) anything.
    if media_type == "text/html" or file == root / SERVICE_WORKER:
        headers["Content-Security-Policy"] = PAGE_CSP
    accepted = _accepted_encodings(accept_encoding)
    for encoding, suffix in ENCODINGS:
        variant = file.with_name(file.name + suffix)
        if encoding in accepted and variant.is_file():
            headers["Content-Encoding"] = encoding
            return FileResponse(variant, media_type=media_type, headers=headers)
    return FileResponse(file, media_type=media_type, headers=headers)


def _accepted_encodings(header: str) -> set[str]:
    """Encodings in an ``Accept-Encoding`` header, minus any refused with ``q=0``."""
    accepted = set()
    for part in header.split(","):
        name, _, params = part.strip().partition(";")
        refused = params.replace(" ", "").lower() in {"q=0", "q=0.0", "q=0.00", "q=0.000"}
        if name and not refused:
            accepted.add(name.lower())
    return accepted
