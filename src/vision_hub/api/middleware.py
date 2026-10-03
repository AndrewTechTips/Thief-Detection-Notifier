"""Pure-ASGI middleware. ``BaseHTTPMiddleware`` is avoided on purpose: it buffers streaming
responses (MJPEG feeds) and breaks contextvar propagation."""

import re
import time
import uuid
from collections.abc import Sequence
from http import HTTPStatus
from typing import ClassVar

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from vision_hub.core.errors import problem_response
from vision_hub.core.logging import get_logger

REQUEST_ID_HEADER = "X-Request-ID"
# Accept caller-supplied IDs only if they are short and log-safe (no spaces, newlines, quotes).
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._:\-]{1,128}")

logger = get_logger(__name__)


def resolve_request_id(scope: Scope) -> str:
    incoming = Headers(scope=scope).get(REQUEST_ID_HEADER)
    if incoming and _VALID_REQUEST_ID.fullmatch(incoming):
        return incoming
    return str(uuid.uuid7())


class RequestContextMiddleware:
    """Assigns a request ID, binds it to every log line, echoes it in the response, writes one
    access-log line per request, and turns unhandled exceptions into a problem+json 500.

    Query strings are never logged: they may carry tokens (e.g. WebSocket tickets). Successful
    requests to ``quiet_paths`` (e.g. health probes) are logged at DEBUG to keep logs readable.
    """

    def __init__(self, app: ASGIApp, quiet_paths: frozenset[str] = frozenset()) -> None:
        self.app = app
        self.quiet_paths = quiet_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        request_id = resolve_request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id

        with structlog.contextvars.bound_contextvars(request_id=request_id):
            if scope["type"] == "websocket":
                await self.app(scope, receive, send)
            else:
                await self._handle_http(scope, receive, send, request_id)

    async def _handle_http(
        self, scope: Scope, receive: Receive, send: Send, request_id: str
    ) -> None:
        started = time.perf_counter()
        status_code = HTTPStatus.INTERNAL_SERVER_ERROR.value
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message["status"])
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception("unhandled_exception")
            if response_started:
                raise  # too late to send a clean error; let the server close the connection
            response = problem_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred.",
                instance=scope["path"],
                request_id=request_id,
            )
            await response(scope, receive, send_with_request_id)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
                log = logger.error
            elif scope["path"] in self.quiet_paths:
                log = logger.debug
            else:
                log = logger.info
            fields: dict[str, object] = {
                "method": scope["method"],
                "path": scope["path"],
                "status": status_code,
                "duration_ms": duration_ms,
                "client": scope["client"][0] if scope.get("client") else None,
            }
            if username := scope["state"].get("username"):  # set by the auth dependency
                fields["user"] = username
            log("request", **fields)


class SecurityHeadersMiddleware:
    """Adds defensive headers to every HTTP response (without overriding ones a route set).

    The strict CSP suits a JSON API; ``relaxed_paths`` (Swagger UI / ReDoc, which load scripts
    from a CDN) are served without it.
    """

    _BASE_HEADERS: ClassVar[dict[str, str]] = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }
    _API_CSP = "default-src 'none'; frame-ancestors 'none'"
    _HSTS = "max-age=63072000; includeSubDomains"

    def __init__(
        self, app: ASGIApp, *, hsts: bool = False, relaxed_paths: frozenset[str] = frozenset()
    ) -> None:
        self.app = app
        self.hsts = hsts
        self.relaxed_paths = relaxed_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        extra = dict(self._BASE_HEADERS)
        if scope["path"] not in self.relaxed_paths:
            extra["Content-Security-Policy"] = self._API_CSP
        if self.hsts:
            extra["Strict-Transport-Security"] = self._HSTS

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in extra.items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class TrustedHostMiddleware:
    """Rejects requests whose ``Host`` header is not allowed (DNS-rebinding and host-header
    attacks). Like Starlette's version, but errors are problem+json like the rest of the API.
    Patterns may start with ``*.`` to match subdomains; ``*`` alone disables the check.
    ``exempt_paths`` (health probes, which expose nothing) are reachable under any host.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: Sequence[str],
        exempt_paths: frozenset[str] = frozenset(),
    ) -> None:
        self.app = app
        self.allowed_hosts = [host.lower() for host in allowed_hosts]
        self.allow_any = "*" in self.allowed_hosts
        self.exempt_paths = exempt_paths

    def is_allowed(self, host_header: str) -> bool:
        host = _hostname(host_header.lower())
        return any(
            host == pattern or (pattern.startswith("*.") and host.endswith(pattern[1:]))
            for pattern in self.allowed_hosts
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] not in {"http", "websocket"}
            or self.allow_any
            or scope["path"] in self.exempt_paths
            or self.is_allowed(Headers(scope=scope).get("host", ""))
        ):
            await self.app(scope, receive, send)
            return

        logger.warning("invalid_host_header", host=Headers(scope=scope).get("host"))
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = problem_response(
            HTTPStatus.BAD_REQUEST,
            detail="Invalid host header.",
            instance=scope["path"],
            request_id=scope.get("state", {}).get("request_id"),
        )
        await response(scope, receive, send)


def _hostname(host_header: str) -> str:
    """Strip the port: ``example.com:8000`` -> ``example.com``, ``[::1]:8000`` -> ``[::1]``."""
    if host_header.startswith("["):
        return host_header[: host_header.find("]") + 1]
    return host_header.split(":", 1)[0]
