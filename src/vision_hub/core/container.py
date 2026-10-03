"""Service container: long-lived objects created at startup and released at shutdown."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TypedDict

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from vision_hub.core.config import Settings, env_name
from vision_hub.core.encryption import SecretBox
from vision_hub.core.logging import get_logger
from vision_hub.core.security import PasswordHasher, TokenService
from vision_hub.domain.auth import Role
from vision_hub.domain.events import CameraEvent, topic_for
from vision_hub.domain.health import HealthCheck
from vision_hub.domain.notifications import Notifier
from vision_hub.infra.auth import InMemoryTicketStore
from vision_hub.infra.bus.memory import InMemoryEventBus
from vision_hub.infra.db.engine import Sessions, create_engine, create_sessions
from vision_hub.infra.db.health import DatabaseHealthCheck
from vision_hub.infra.db.migrate import upgrade_to_head
from vision_hub.infra.db.repositories.devices import SqlDeviceRepository
from vision_hub.infra.db.repositories.tokens import SqlTokenRevocationStore
from vision_hub.infra.db.repositories.users import SqlUserRepository
from vision_hub.infra.notifiers.email import EmailNotifier
from vision_hub.infra.process_lock import ProcessLock
from vision_hub.infra.rate_limit import RateLimiter
from vision_hub.realtime.connections import ConnectionManager
from vision_hub.services.auth import AuthService
from vision_hub.services.devices import DeviceService
from vision_hub.services.notifications import NotificationService
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.fleet import DeviceSpec, load_fleet
from vision_hub.vision.manager import CameraManager, camera_worker_factory
from vision_hub.vision.sources import Backoff
from vision_hub.vision.worker import EncodingSettings

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class Container:
    """Typed registry of application services, resolved in routes via ``ContainerDep``.

    New services are added as fields and wired in ``build_container``; nothing is stored in
    module-level globals.
    """

    settings: Settings
    database: AsyncEngine
    sessions: Sessions
    auth: AuthService
    auth_rate_limiter: RateLimiter
    cameras: CameraManager
    bus: InMemoryEventBus[CameraEvent]
    notifications: NotificationService
    devices: DeviceService
    tickets: InMemoryTicketStore
    realtime: ConnectionManager
    health_checks: tuple[HealthCheck, ...] = ()


class LifespanState(TypedDict):
    """Starlette copies this into ``request.state`` for every request and WebSocket."""

    container: Container


@asynccontextmanager
async def build_container(settings: Settings) -> AsyncIterator[Container]:
    """Create services on startup and release them, in reverse order, on shutdown.

    Fails fast (the app does not start) if another process owns the cameras, the devices file
    is invalid, or the database stays unreachable.
    """
    async with AsyncExitStack() as stack:
        lock = ProcessLock(settings.vision.lock_file)
        lock.acquire()
        stack.callback(lock.release)

        engine = create_engine(settings.db)
        stack.push_async_callback(engine.dispose)
        await _wait_for_database(engine)
        if settings.db.migrate_on_startup:
            await upgrade_to_head(engine)
        sessions = create_sessions(engine)

        users = SqlUserRepository(sessions)
        await _bootstrap_users(users, settings)
        detection_defaults = DetectionConfig.from_settings(settings.vision)
        device_repository = SqlDeviceRepository(
            sessions, SecretBox.from_config(settings.security, allow_key_file=not settings.is_prod)
        )
        seeded = await device_repository.add_missing(_load_fleet(settings, detection_defaults))
        if seeded:
            logger.info("devices_seeded_from_file", devices=list(seeded))

        bus: InMemoryEventBus[CameraEvent] = InMemoryEventBus()
        stack.callback(bus.close)
        realtime = ConnectionManager(bus, settings.realtime)
        stack.callback(realtime.close_all)  # runs before bus.close: clients get 1001
        notifications = NotificationService(
            bus,
            _build_notifiers(settings),
            device_name=lambda device_id: devices.name_of(device_id),
            cooldown_seconds=settings.vision.alert_cooldown_seconds,
        )
        notifications.start()  # subscribes before any camera can publish
        stack.push_async_callback(notifications.stop)

        cameras = CameraManager(
            on_event=lambda event: bus.publish(topic_for(event), event),
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
        devices = DeviceService(
            device_repository,
            cameras,
            detection_defaults=detection_defaults,
            media_dir=settings.vision.media_dir,
        )

        container = Container(
            settings=settings,
            database=engine,
            sessions=sessions,
            auth=AuthService(
                users=users,
                hasher=PasswordHasher(),
                tokens=TokenService(settings.security),
                revocations=SqlTokenRevocationStore(sessions),
            ),
            auth_rate_limiter=RateLimiter(settings.security.auth_rate_limit),
            cameras=cameras,
            bus=bus,
            notifications=notifications,
            devices=devices,
            tickets=InMemoryTicketStore(settings.security.ticket_ttl_seconds),
            realtime=realtime,
            health_checks=(DatabaseHealthCheck(engine),),
        )
        await devices.start_enabled()

        running = sum(cameras.is_running(device_id) for device_id in cameras.device_ids)
        logger.info("container_started", cameras=running)
        try:
            yield container
        finally:
            logger.info("container_stopping")
    logger.info("container_stopped")


async def _wait_for_database(engine: AsyncEngine, attempts: int = 10) -> None:
    """Containers often start before the database accepts connections; retry briefly."""
    backoff = Backoff(initial=0.5, maximum=5)
    for attempt in range(1, attempts + 1):
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except (OperationalError, OSError) as exc:
            if attempt == attempts:
                raise
            delay = backoff.next_delay()
            logger.warning(
                "database_unavailable", attempt=attempt, retry_in_s=round(delay, 1), error=str(exc)
            )
            await asyncio.sleep(delay)
        else:
            return


async def _bootstrap_users(users: SqlUserRepository, settings: Settings) -> None:
    security = settings.security
    if security.admin_password_hash is not None:
        created = await users.create_if_missing(
            security.admin_username, security.admin_password_hash.get_secret_value(), Role.ADMIN
        )
        if created:
            logger.info("admin_created", username=security.admin_username)
    if await users.count() == 0:
        logger.warning(
            "no_users",
            hint=f"create one with `vision-hub create-user NAME --role admin`, or set "
            f"{env_name('security', 'admin_password_hash')}; all logins will fail until then",
        )


def _load_fleet(settings: Settings, defaults: DetectionConfig) -> list[DeviceSpec]:
    if settings.vision.devices_file is None:
        return []
    return load_fleet(settings.vision.devices_file, defaults=defaults)


def _build_notifiers(settings: Settings) -> list[Notifier]:
    if not settings.smtp.enabled:
        logger.info("email_alerts_disabled", hint=f"set {env_name('smtp', 'enabled')}=true")
        return []
    return [EmailNotifier(settings.smtp)]
