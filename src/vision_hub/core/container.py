"""Service container: long-lived objects created at startup and released at shutdown."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TypedDict

from vision_hub.core.config import Settings, env_name
from vision_hub.core.logging import get_logger
from vision_hub.core.security import PasswordHasher, TokenService
from vision_hub.domain.health import HealthCheck
from vision_hub.infra.auth import InMemoryTokenRevocationStore, SettingsUserRepository
from vision_hub.infra.process_lock import ProcessLock
from vision_hub.infra.rate_limit import RateLimiter
from vision_hub.services.auth import AuthService
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.fleet import DeviceSpec, load_fleet
from vision_hub.vision.manager import CameraManager, camera_worker_factory
from vision_hub.vision.worker import EncodingSettings

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class Container:
    """Typed registry of application services, resolved in routes via ``ContainerDep``.

    New services are added as fields and wired in ``build_container``; nothing is stored in
    module-level globals.
    """

    settings: Settings
    auth: AuthService
    auth_rate_limiter: RateLimiter
    cameras: CameraManager
    fleet: tuple[DeviceSpec, ...] = ()
    health_checks: tuple[HealthCheck, ...] = ()


class LifespanState(TypedDict):
    """Starlette copies this into ``request.state`` for every request and WebSocket."""

    container: Container


@asynccontextmanager
async def build_container(settings: Settings) -> AsyncIterator[Container]:
    """Create services on startup and release them, in reverse order, on shutdown.

    Fails fast (the app does not start) if another process owns the cameras or the devices
    file is invalid.
    """
    async with AsyncExitStack() as stack:
        lock = ProcessLock(settings.vision.lock_file)
        lock.acquire()
        stack.callback(lock.release)

        fleet = _load_fleet(settings)
        cameras = CameraManager(
            worker_factory=camera_worker_factory(
                default_fps=settings.vision.target_fps,
                encoding=EncodingSettings(
                    stream_jpeg_quality=settings.vision.stream_jpeg_quality,
                    stream_max_width=settings.vision.stream_max_width,
                    snapshot_jpeg_quality=settings.storage.jpeg_quality,
                ),
            ),
        )
        stack.push_async_callback(cameras.stop_all)

        container = Container(
            settings=settings,
            auth=_build_auth(settings),
            auth_rate_limiter=RateLimiter(settings.security.auth_rate_limit),
            cameras=cameras,
            fleet=fleet,
        )
        for spec in fleet:
            if spec.enabled:
                await cameras.start(spec)

        logger.info("container_started", cameras=sum(spec.enabled for spec in fleet))
        try:
            yield container
        finally:
            logger.info("container_stopping")
    logger.info("container_stopped")


def _load_fleet(settings: Settings) -> tuple[DeviceSpec, ...]:
    if settings.vision.devices_file is None:
        return ()
    defaults = DetectionConfig.from_settings(settings.vision)
    return tuple(load_fleet(settings.vision.devices_file, defaults=defaults))


def _build_auth(settings: Settings) -> AuthService:
    users = SettingsUserRepository(settings.security)
    if not users.has_users:
        logger.warning(
            "no_admin_configured",
            hint=f"set {env_name('security', 'admin_password_hash')} "
            "(generate it with `vision-hub hash-password`); all logins will fail",
        )
    return AuthService(
        users=users,
        hasher=PasswordHasher(),
        tokens=TokenService(settings.security),
        revocations=InMemoryTokenRevocationStore(),
    )
