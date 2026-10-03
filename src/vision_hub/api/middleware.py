"""Pure-ASGI middleware. ``BaseHTTPMiddleware`` is avoided on purpose: it buffers streaming
responses (MJPEG feeds) and breaks contextvar propagation."""

import re
import time
import uuid
from http import HTTPStatus

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

    Query strings are never logged: they may carry tokens (e.g. WebSocket tickets).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

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
                status_code = message["status"]
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
            log = logger.error if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR else logger.info
            log(
                "request",
                method=scope["method"],
                path=scope["path"],
                status=status_code,
                duration_ms=duration_ms,
                client=scope["client"][0] if scope.get("client") else None,
            )
