"""Service container: long-lived objects created at startup and released at shutdown."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TypedDict

from vision_hub.core.config import Settings, env_name
from vision_hub.core.logging import get_logger
from vision_hub.core.security import PasswordHasher, TokenService
from vision_hub.domain.health import HealthCheck
from vision_hub.infra.auth import InMemoryTokenRevocationStore, SettingsUserRepository
from vision_hub.infra.rate_limit import RateLimiter
from vision_hub.services.auth import AuthService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class Container:
    """Typed registry of application services, resolved in routes via ``ContainerDep``.

    New services (event bus, camera manager, DB engine, ...) are added as fields and wired in
    ``build_container``; nothing is stored in module-level globals.
    """

    settings: Settings
    auth: AuthService
    auth_rate_limiter: RateLimiter
    health_checks: tuple[HealthCheck, ...] = ()


class LifespanState(TypedDict):
    """Starlette copies this into ``request.state`` for every request and WebSocket."""

    container: Container


@asynccontextmanager
async def build_container(settings: Settings) -> AsyncIterator[Container]:
    """Create services on startup and release them, in reverse order, on shutdown."""
    users = SettingsUserRepository(settings.security)
    if not users.has_users:
        logger.warning(
            "no_admin_configured",
            hint=f"set {env_name('security', 'admin_password_hash')} "
            "(generate it with `vision-hub hash-password`); all logins will fail",
        )
    container = Container(
        settings=settings,
        auth=AuthService(
            users=users,
            hasher=PasswordHasher(),
            tokens=TokenService(settings.security),
            revocations=InMemoryTokenRevocationStore(),
        ),
        auth_rate_limiter=RateLimiter(settings.security.auth_rate_limit),
    )
    logger.info("container_started")
    try:
        yield container
    finally:
        logger.info("container_stopped")
