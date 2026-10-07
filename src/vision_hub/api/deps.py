"""Reusable ``Annotated`` dependencies: routes declare what they need by type annotation
(e.g. ``settings: SettingsDep``) instead of reaching for globals."""

from collections.abc import Callable
from typing import Annotated, cast
from urllib.parse import urlencode

from fastapi import Depends, Query
from fastapi.security import OAuth2PasswordBearer
from starlette.requests import HTTPConnection

from vision_hub.api.v1 import API_V1_PREFIX
from vision_hub.core.config import Settings
from vision_hub.core.container import Container
from vision_hub.core.errors import AuthenticationError, NotFoundError, PermissionDeniedError
from vision_hub.domain.auth import Principal, Role
from vision_hub.domain.health import HealthCheck
from vision_hub.services.audit import Auditor
from vision_hub.services.auth import AuthService
from vision_hub.services.devices import DeviceService
from vision_hub.services.events import EventService
from vision_hub.services.push import PushService


def get_container(connection: HTTPConnection) -> Container:
    """Works for both HTTP requests and WebSockets (both are ``HTTPConnection``)."""
    try:
        container = connection.state.container
    except AttributeError:
        msg = "Service container unavailable: the application lifespan has not started"
        raise RuntimeError(msg) from None
    return cast("Container", container)


ContainerDep = Annotated[Container, Depends(get_container)]


def get_app_settings(container: ContainerDep) -> Settings:
    return container.settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_health_checks(container: ContainerDep) -> tuple[HealthCheck, ...]:
    return container.health_checks


HealthChecksDep = Annotated[tuple[HealthCheck, ...], Depends(get_health_checks)]


def get_auth_service(container: ContainerDep) -> AuthService:
    return container.auth


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_device_service(container: ContainerDep) -> DeviceService:
    return container.devices


DeviceServiceDep = Annotated[DeviceService, Depends(get_device_service)]


def get_event_service(container: ContainerDep) -> EventService:
    return container.events


EventServiceDep = Annotated[EventService, Depends(get_event_service)]


def get_push_service(container: ContainerDep) -> PushService:
    if container.push is None:
        msg = "Push notifications are turned off on this hub."
        raise NotFoundError(msg)
    return container.push


PushServiceDep = Annotated[PushService, Depends(get_push_service)]


def get_auditor(container: ContainerDep) -> Auditor:
    return container.auditor


AuditorDep = Annotated[Auditor, Depends(get_auditor)]

# auto_error=False: missing tokens raise our AuthenticationError (problem+json), not FastAPI's.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{API_V1_PREFIX}/auth/token", auto_error=False)


async def get_current_principal(
    connection: HTTPConnection,
    auth: AuthServiceDep,
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> Principal:
    if not token:
        raise AuthenticationError("Missing bearer token.")
    principal = auth.authenticate(token)
    # Picked up by RequestContextMiddleware for the access log.
    connection.state.username = principal.username
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


async def get_ticket_or_bearer_principal(
    connection: HTTPConnection,
    container: ContainerDep,
    token: Annotated[str | None, Depends(oauth2_scheme)],
    ticket: Annotated[
        str | None,
        Query(max_length=128, description="Single-use ticket from POST /api/v1/auth/tickets"),
    ] = None,
) -> Principal:
    """For endpoints browsers open without custom headers (``<img src>`` MJPEG streams)."""
    if token:
        principal = container.auth.authenticate(token)
    elif ticket:
        consumed = container.tickets.consume(ticket)
        if consumed is None:
            raise AuthenticationError("Ticket is invalid, expired or already used.")
        principal = consumed
    else:
        raise AuthenticationError("Missing bearer token or ticket.")
    connection.state.username = principal.username
    return principal


def require_role(role: Role) -> Callable[[Principal], Principal]:
    """Dependency factory: ``Depends(require_role(Role.ADMIN))``. Higher roles also pass."""

    def check_role(principal: CurrentPrincipal) -> Principal:
        if not principal.role.includes(role):
            raise PermissionDeniedError(f"This action requires the '{role}' role.")
        return principal

    return check_role


async def require_signature_or_bearer(
    connection: HTTPConnection,
    container: ContainerDep,
    token: Annotated[str | None, Depends(oauth2_scheme)],
    expires: Annotated[int | None, Query(description="From a signed link")] = None,
    signature: Annotated[
        str | None, Query(max_length=128, description="From a signed link")
    ] = None,
) -> None:
    """For immutable resources linked from API responses (event snapshots and clips): a valid
    signed link is enough, so ``<img>`` and ``<video>`` tags work without a token or ticket."""
    if token:
        principal = container.auth.authenticate(token)
        connection.state.username = principal.username
        return
    if expires is not None and signature is not None:
        # The signature covers the path and every other query parameter, in order.
        params = [
            (key, value)
            for key, value in connection.query_params.multi_items()
            if key not in {"expires", "signature"}
        ]
        resource = connection.url.path + (f"?{urlencode(params)}" if params else "")
        if container.url_signer.verify(resource, expires, signature):
            return
        raise AuthenticationError("Signed link is invalid or has expired.")
    raise AuthenticationError("Missing bearer token or signed link.")


AdminPrincipal = Annotated[Principal, Depends(require_role(Role.ADMIN))]
# For routes that only need the check: ``@router.post(..., dependencies=[AdminOnly])``.
AdminOnly = Depends(require_role(Role.ADMIN))
