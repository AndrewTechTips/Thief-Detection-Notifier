"""Domain exceptions and their translation into RFC 9457 ``application/problem+json`` responses."""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, ClassVar, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from vision_hub.core.logging import get_logger
from vision_hub.schemas.problem import FieldError, ProblemDetail

PROBLEM_JSON = "application/problem+json"
PROBLEM_TYPE_PREFIX = "urn:vision-hub:problem:"

logger = get_logger(__name__)


class AppError(Exception):
    """Base for errors that map to a well-defined HTTP problem.

    Subclasses set the class-level ``status_code``, ``code`` and ``title``; instances carry an
    occurrence-specific ``detail`` plus optional extension members (e.g. ``device_id``).
    """

    status_code: ClassVar[int] = HTTPStatus.INTERNAL_SERVER_ERROR
    code: ClassVar[str] = "internal-error"
    title: ClassVar[str] = "Internal Server Error"
    headers: ClassVar[Mapping[str, str]] = {}

    def __init__(
        self,
        detail: str | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        **extensions: Any,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.extensions = extensions
        self.response_headers = {**type(self).headers, **(headers or {})}

    @property
    def type_uri(self) -> str:
        return f"{PROBLEM_TYPE_PREFIX}{self.code}"


class BadRequestError(AppError):
    status_code = HTTPStatus.BAD_REQUEST
    code = "bad-request"
    title = "Bad Request"


class InvalidCursorError(BadRequestError):
    code = "invalid-cursor"
    title = "Invalid Pagination Cursor"

    def __init__(self, detail: str | None = None, **extensions: Any) -> None:
        super().__init__(detail or "Use the next_cursor value from a previous page.", **extensions)


class NotFoundError(AppError):
    status_code = HTTPStatus.NOT_FOUND
    code = "not-found"
    title = "Resource Not Found"


class ConflictError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "conflict"
    title = "Conflict"


class AuthenticationError(AppError):
    status_code = HTTPStatus.UNAUTHORIZED
    code = "unauthenticated"
    title = "Authentication Required"
    headers: ClassVar[Mapping[str, str]] = {"WWW-Authenticate": "Bearer"}


class PermissionDeniedError(AppError):
    status_code = HTTPStatus.FORBIDDEN
    code = "permission-denied"
    title = "Permission Denied"


class RateLimitedError(AppError):
    status_code = HTTPStatus.TOO_MANY_REQUESTS
    code = "rate-limited"
    title = "Too Many Requests"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            f"Too many attempts. Retry in {retry_after_seconds} seconds.",
            headers={"Retry-After": str(retry_after_seconds)},
        )


class ServiceUnavailableError(AppError):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "service-unavailable"
    title = "Service Unavailable"


def status_phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


def problem_response(
    status: int,
    *,
    title: str | None = None,
    type_: str = "about:blank",
    detail: str | None = None,
    instance: str | None = None,
    request_id: str | None = None,
    errors: list[FieldError] | None = None,
    headers: Mapping[str, str] | None = None,
    extensions: Mapping[str, Any] | None = None,
) -> JSONResponse:
    problem = ProblemDetail(
        type=type_,
        title=title or status_phrase(status),
        status=status,
        detail=detail,
        instance=instance,
        request_id=request_id,
        errors=errors,
        **(extensions or {}),
    )
    return JSONResponse(
        problem.model_dump(mode="json", exclude_none=True),
        status_code=status,
        headers=dict(headers or {}),
        media_type=PROBLEM_JSON,
    )


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    error = cast("AppError", exc)  # guaranteed by the handler registration
    log = logger.error if error.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR else logger.info
    log("app_error", code=error.code, status=error.status_code, detail=error.detail)
    return problem_response(
        error.status_code,
        title=error.title,
        type_=error.type_uri,
        detail=error.detail,
        instance=request.url.path,
        request_id=_request_id(request),
        headers=error.response_headers,
        extensions=error.extensions,
    )


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    error = cast("StarletteHTTPException", exc)
    phrase = status_phrase(error.status_code)
    raw_detail: object = error.detail  # fastapi.HTTPException allows any JSON value here
    # Omit empty or default details (Starlette uses "" for non-standard status codes).
    detail = raw_detail if isinstance(raw_detail, str) and raw_detail not in {"", phrase} else None
    return problem_response(
        error.status_code,
        detail=detail,
        instance=request.url.path,
        request_id=_request_id(request),
        headers=error.headers,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    error = cast("RequestValidationError", exc)
    # Rebuild errors without "input"/"ctx": echoing input could leak passwords or tokens.
    errors = [
        FieldError(loc=list(item["loc"]), msg=item["msg"], type=item["type"])
        for item in error.errors()
    ]
    return problem_response(
        HTTPStatus.UNPROCESSABLE_CONTENT,
        title="Validation Failed",
        detail="The request contains invalid data.",
        instance=request.url.path,
        request_id=_request_id(request),
        errors=errors,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Unhandled exceptions are converted by ``RequestContextMiddleware``, which owns the
    request ID and is the only layer that sees them before the server does."""
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
