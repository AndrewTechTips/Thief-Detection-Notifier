"""Reusable ``Annotated`` dependencies: routes declare what they need by type annotation
(e.g. ``settings: SettingsDep``) instead of reaching for globals."""

from collections.abc import Callable
from typing import Annotated, cast

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from starlette.requests import HTTPConnection

from vision_hub.api.v1 import API_V1_PREFIX
from vision_hub.core.config import Settings
from vision_hub.core.container import Container
from vision_hub.core.errors import AuthenticationError, PermissionDeniedError
from vision_hub.domain.auth import Principal, Role
from vision_hub.domain.health import HealthCheck
from vision_hub.services.auth import AuthService


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


def require_role(role: Role) -> Callable[[Principal], Principal]:
    """Dependency factory: ``Depends(require_role(Role.ADMIN))``. Higher roles also pass."""

    def check_role(principal: CurrentPrincipal) -> Principal:
        if not principal.role.includes(role):
            raise PermissionDeniedError(f"This action requires the '{role}' role.")
        return principal

    return check_role


AdminPrincipal = Annotated[Principal, Depends(require_role(Role.ADMIN))]
