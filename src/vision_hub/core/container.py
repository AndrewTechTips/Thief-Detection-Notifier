"""Service container: long-lived objects created at startup and released at shutdown."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import TypedDict

import cv2
from prometheus_client.core import Metric
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from vision_hub.api.v1 import API_V1_PREFIX
from vision_hub.core.backoff import Backoff
from vision_hub.core.config import (
    ClipConfig,
    NotificationsConfig,
    PersonConfig,
    PushConfig,
    Settings,
    env_name,
)
from vision_hub.core.encryption import SecretBox
from vision_hub.core.lifecycle import Lifecycle, ShutdownCheck
from vision_hub.core.logging import get_logger
from vision_hub.core.metrics import Metrics, StateCollector, gauge
from vision_hub.core.security import PasswordHasher, TokenService, UrlSigner, utc_now
from vision_hub.core.tasks import TaskSupervisor
from vision_hub.domain.audit import AuditAction, AuditTarget
from vision_hub.domain.auth import Role
from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.events import CameraEvent, topic_for
from vision_hub.domain.health import HealthCheck
from vision_hub.domain.notifications import Notifier, RetryPolicy
from vision_hub.domain.storage import SnapshotKind
from vision_hub.infra.auth import InMemoryTicketStore
from vision_hub.infra.bus.memory import InMemoryEventBus
from vision_hub.infra.db.engine import Sessions, create_engine, create_sessions
from vision_hub.infra.db.health import DatabaseHealthCheck
from vision_hub.infra.db.migrate import upgrade_to_head
from vision_hub.infra.db.repositories.audit import SqlAuditLog
from vision_hub.infra.db.repositories.devices import SqlDeviceRepository
from vision_hub.infra.db.repositories.events import SqlEventRepository
from vision_hub.infra.db.repositories.notifications import SqlNotificationOutbox
from vision_hub.infra.db.repositories.push import SqlPushSubscriptionRepository
from vision_hub.infra.db.repositories.tokens import SqlTokenRevocationStore
from vision_hub.infra.db.repositories.users import SqlUserRepository
from vision_hub.infra.notifiers.email import EmailNotifier
from vision_hub.infra.notifiers.webpush import (
    VapidKey,
    WebPushNotifier,
    load_or_create_vapid_key,
)
from vision_hub.infra.process_lock import ProcessLock
from vision_hub.infra.rate_limit import RateLimiter
from vision_hub.infra.storage.local import LocalSnapshotStore
from vision_hub.realtime.connections import ConnectionManager
from vision_hub.schemas.events import snapshot_resource
from vision_hub.services.audit import SYSTEM_ACTOR, Auditor
from vision_hub.services.auth import AuthService
from vision_hub.services.devices import DeviceService
from vision_hub.services.events import EventRecorder, EventService, RetentionService
from vision_hub.services.notifications import NotificationService
from vision_hub.services.push import PushService
from vision_hub.vision.clip import ClipSettings
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.fleet import DeviceSpec, load_fleet
from vision_hub.vision.manager import CameraManager, camera_worker_factory
from vision_hub.vision.persons import PersonChecker, load_person_detector
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
    events: EventService
    auditor: Auditor
    metrics: Metrics
    lifecycle: Lifecycle
    url_signer: UrlSigner
    tickets: InMemoryTicketStore
    realtime: ConnectionManager
    push: PushService | None = None  # None when push is turned off
    health_checks: tuple[HealthCheck, ...] = ()


class LifespanState(TypedDict):
    """Starlette copies this into ``request.state`` for every request and WebSocket."""

    container: Container


@asynccontextmanager
async def build_container(
    settings: Settings, lifecycle: Lifecycle | None = None, metrics: Metrics | None = None
) -> AsyncIterator[Container]:
    """Create services on startup and release them, in reverse order, on shutdown.

    Fails fast (the app does not start) if another process owns the cameras, the devices file
    is invalid, or the database stays unreachable.

    Startup recovers from an abrupt stop: events that never ended are flagged as interrupted
    and undelivered alerts are retried. Shutdown order: cameras stop (closing their events),
    the recorder stores and publishes what is left, notifications get a last chance to send,
    WebSocket clients are closed, background tasks end, then the database pool closes.
    """
    lifecycle = lifecycle or Lifecycle()
    metrics = metrics or Metrics()
    async with AsyncExitStack() as stack:
        lock = ProcessLock(settings.vision.lock_file)
        lock.acquire()
        stack.callback(lock.release)

        engine = create_engine(settings.db)
        stack.push_async_callback(engine.dispose)
        await _wait_for_database(engine)
        if settings.db.migrate_on_startup:
            await upgrade_to_head(engine)
        # Registered here so it runs after every service has stopped, before the pool closes.
        tasks = TaskSupervisor(metrics=metrics)
        stack.push_async_callback(tasks.aclose)
        sessions = create_sessions(engine)
        auditor = Auditor(SqlAuditLog(sessions))

        users = SqlUserRepository(sessions)
        await _bootstrap_users(users, settings, auditor)
        detection_defaults = DetectionConfig.from_settings(settings.vision)
        device_repository = SqlDeviceRepository(
            sessions, SecretBox.from_config(settings.security, allow_key_file=not settings.is_prod)
        )
        seeded = await device_repository.add_missing(_load_fleet(settings, detection_defaults))
        if seeded:
            logger.info("devices_seeded_from_file", devices=list(seeded))

        snapshots = LocalSnapshotStore(settings.storage.snapshots_dir)
        event_repository = SqlEventRepository(sessions)
        events = EventService(event_repository, snapshots)
        if interrupted := await event_repository.mark_interrupted():
            logger.warning("interrupted_events_recovered", count=interrupted)

        bus: InMemoryEventBus[CameraEvent] = InMemoryEventBus()
        stack.callback(bus.close)
        realtime = ConnectionManager(bus, settings.realtime, history=events, metrics=metrics)
        stack.callback(realtime.close_all)  # runs before bus.close: clients get 1001
        url_signer = UrlSigner(
            settings.security.jwt_secret, ttl_seconds=settings.security.signed_url_ttl_seconds
        )
        notifiers = _build_notifiers(settings)
        push: PushService | None = None
        if settings.push.enabled:
            subscriptions = SqlPushSubscriptionRepository(sessions)
            web_push = _build_web_push(settings, subscriptions, url_signer)
            notifiers.append(web_push)
            push = PushService(subscriptions, web_push, allowed_hosts=settings.push.allowed_hosts)
        notifications = NotificationService(
            bus,
            notifiers,
            outbox=SqlNotificationOutbox(sessions),
            evidence=events.evidence,
            device_name=lambda device_id: devices.name_of(device_id),
            cooldown_seconds=settings.vision.alert_cooldown_seconds,
            policy=_retry_policy(settings.notifications),
            tasks=tasks,
            metrics=metrics,
        )
        metrics.on_scrape(notifications.refresh_metrics)
        await notifications.start()  # subscribes before any camera can publish
        stack.push_async_callback(notifications.stop)

        # Persist first, then publish: stops after the cameras (so their final events are kept)
        # and before notifications drain.
        recorder = EventRecorder(
            event_repository,
            snapshots,
            publish=lambda event: bus.publish(topic_for(event), event),
            tasks=tasks,
            metrics=metrics,
        )
        recorder.start()
        stack.push_async_callback(recorder.stop)

        cv2.setNumThreads(settings.vision.opencv_threads)
        persons = await _person_checker(settings.persons)
        if persons is not None:
            # Runs after the cameras have stopped (callbacks run in reverse): their last events
            # still get their person verdict and reach the recorder.
            stack.push_async_callback(asyncio.to_thread, persons.close)
        cameras = CameraManager(
            on_event=recorder.submit,
            metrics=metrics,
            worker_factory=camera_worker_factory(
                default_fps=settings.vision.target_fps,
                encoding=EncodingSettings(
                    stream_jpeg_quality=settings.vision.stream_jpeg_quality,
                    stream_max_width=settings.vision.stream_max_width,
                    snapshot_jpeg_quality=settings.storage.jpeg_quality,
                    thumbnail_width=settings.storage.thumbnail_width,
                ),
                metrics=metrics,
                clips=_clip_settings(settings.clips),
                persons=persons,
            ),
        )
        stack.push_async_callback(cameras.stop_all)
        lifecycle.on_shutdown(cameras.close_streams)
        devices = DeviceService(
            device_repository,
            cameras,
            auditor=auditor,
            detection_defaults=detection_defaults,
            media_dir=settings.vision.media_dir,
        )
        retention = RetentionService(
            event_repository,
            snapshots,
            default_days=settings.storage.retention_days,
            overrides=lambda: _retention_overrides(device_repository),
            interval_seconds=settings.storage.retention_check_minutes * 60,
            tasks=tasks,
        )
        retention.start()
        stack.push_async_callback(retention.stop)

        collector = StateCollector(lambda: _live_state(cameras, realtime))
        metrics.add_collector(collector)
        stack.callback(metrics.registry.unregister, collector)
        loop_monitor = tasks.spawn("event-loop-monitor", metrics.watch_event_loop)
        stack.push_async_callback(_cancel, loop_monitor)

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
            events=events,
            auditor=auditor,
            metrics=metrics,
            lifecycle=lifecycle,
            url_signer=url_signer,
            tickets=InMemoryTicketStore(settings.security.ticket_ttl_seconds),
            realtime=realtime,
            push=push,
            health_checks=(DatabaseHealthCheck(engine), ShutdownCheck(lifecycle)),
        )
        await devices.start_enabled()

        running = sum(cameras.is_running(device_id) for device_id in cameras.device_ids)
        logger.info("container_started", cameras=running)
        try:
            yield container
        finally:
            lifecycle.begin_shutdown()  # no-op when the server already signalled it
            logger.info("container_stopping")
    logger.info("container_stopped")


async def _wait_for_database(engine: AsyncEngine, attempts: int = 10) -> None:
    """Containers often start before the database accepts connections; retry briefly."""
    backoff = Backoff(initial=0.5, maximum=5)
    attempt = 1
    while True:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except (OperationalError, OSError) as exc:
            if attempt >= attempts:
                raise
            delay = backoff.next_delay()
            logger.warning(
                "database_unavailable", attempt=attempt, retry_in_s=round(delay, 1), error=str(exc)
            )
            await asyncio.sleep(delay)
            attempt += 1
        else:
            return


async def _bootstrap_users(users: SqlUserRepository, settings: Settings, auditor: Auditor) -> None:
    security = settings.security
    if security.admin_password_hash is not None:
        created = await users.create_if_missing(
            security.admin_username, security.admin_password_hash.get_secret_value(), Role.ADMIN
        )
        if created:
            logger.info("admin_created", username=security.admin_username)
            await auditor.record(
                SYSTEM_ACTOR,
                AuditAction.USER_CREATED,
                AuditTarget.USER,
                security.admin_username,
                role=Role.ADMIN,
                source=env_name("security", "admin_password_hash"),
            )
    if await users.count() == 0:
        logger.warning(
            "no_users",
            hint=f"create one with `vision-hub create-user NAME --role admin`, or set "
            f"{env_name('security', 'admin_password_hash')}; all logins will fail until then",
        )


async def _retention_overrides(devices: SqlDeviceRepository) -> dict[str, int]:
    return {d.id: d.retention_days for d in await devices.list() if d.retention_days is not None}


def _load_fleet(settings: Settings, defaults: DetectionConfig) -> list[DeviceSpec]:
    if settings.vision.devices_file is None:
        return []
    return load_fleet(settings.vision.devices_file, defaults=defaults)


def _live_state(cameras: CameraManager, realtime: ConnectionManager) -> Iterator[Metric]:
    """Per-camera health, read at scrape time."""
    status = gauge("camera_status", "1 for the camera's current status.", ["device", "status"])
    viewers = gauge("camera_live_viewers", "Open MJPEG streams.", ["device"])
    frame_age = gauge(
        "camera_last_frame_age_seconds",
        "Age of the newest live frame (refreshed at least every second while running).",
        ["device"],
    )
    now = utc_now()
    for device_id in cameras.device_ids:
        current = cameras.status(device_id)
        for state in DeviceStatus:
            status.add_metric([device_id, state.value], float(state is current))
        viewers.add_metric([device_id], cameras.viewers(device_id))
        if cameras.is_running(device_id) and (latest := cameras.frames(device_id).latest):
            frame_age.add_metric([device_id], (now - latest.captured_at).total_seconds())
    clients = gauge("websocket_clients", "Connected WebSocket clients.", [])
    clients.add_metric([], realtime.active)
    yield from (status, viewers, frame_age, clients)


async def _cancel(task: asyncio.Task[None]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def _clip_settings(config: ClipConfig) -> ClipSettings | None:
    if not config.enabled:
        return None
    return ClipSettings(
        pre_roll_seconds=config.pre_roll_seconds, max_seconds=config.max_seconds, width=config.width
    )


async def _person_checker(config: PersonConfig) -> PersonChecker | None:
    """One detector and one background thread for every camera, loaded off the event loop."""
    if not config.enabled:
        logger.info("person_detection_disabled")
        return None
    detector = await asyncio.to_thread(load_person_detector, config.model_path)
    if detector is None:
        return None
    return PersonChecker(
        detector,
        threshold=config.threshold,
        check_interval_seconds=config.check_interval_seconds,
        max_checks=config.max_checks,
    )


def _retry_policy(config: NotificationsConfig) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=config.max_attempts,
        initial_delay=timedelta(seconds=config.retry_initial_seconds),
        max_delay=timedelta(seconds=config.retry_max_seconds),
        max_age=timedelta(hours=config.max_age_hours),
    )


def _build_notifiers(settings: Settings) -> list[Notifier]:
    if not settings.smtp.enabled:
        logger.info("email_alerts_disabled", hint=f"set {env_name('smtp', 'enabled')}=true")
        return []
    return [EmailNotifier(settings.smtp)]


def _build_web_push(
    settings: Settings, subscriptions: SqlPushSubscriptionRepository, signer: UrlSigner
) -> WebPushNotifier:
    config = settings.push

    def snapshot_link(event_id: str) -> str:
        # Relative: the service worker resolves it against the dashboard's own origin.
        resource = snapshot_resource(API_V1_PREFIX, event_id, SnapshotKind.ANNOTATED)
        expires, signature = signer.sign(resource)
        return f"{resource}&expires={expires}&signature={signature}"

    return WebPushNotifier(
        subscriptions,
        _vapid_key(config),
        subject=_push_subject(settings),
        ttl_seconds=config.ttl_seconds,
        timeout_seconds=config.timeout_seconds,
        snapshot_link=snapshot_link,
    )


def _vapid_key(config: PushConfig) -> VapidKey:
    if config.vapid_private_key is not None:
        return VapidKey.from_text(config.vapid_private_key.get_secret_value())
    return load_or_create_vapid_key(config.vapid_key_file)


def _push_subject(settings: Settings) -> str:
    """Push services contact this address about problems; Apple's rejects missing ones."""
    if settings.push.subject:
        return settings.push.subject
    if sender := settings.smtp.effective_sender:
        return f"mailto:{sender}"
    logger.warning(
        "push_subject_missing",
        hint=f"set {env_name('push', 'subject')} to a mailto: address; Safari's push service "
        "may refuse alerts without a real contact",
    )
    return "mailto:vision-hub@localhost"
