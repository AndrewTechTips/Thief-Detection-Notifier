"""Turns finished motion events into alerts on every configured channel.

* Outbox: each alert is stored as one pending delivery per channel before anything is sent, so
  deliveries survive crashes and restarts. Delivery is at least once: an alert sent just before
  a crash may be sent again.
* Per-device cooldown, so one busy camera cannot flood an inbox (events themselves are never
  dropped; only the alert is suppressed). Restored from the outbox after a restart.
* Cameras set to alert on people only mark events without one as not alerting; those are
  skipped.
* A dispatcher sends due deliveries concurrently, so a slow channel never delays another;
  transient failures are retried with growing delays, permanent ones (bad credentials) are not,
  and alerts older than the policy's ``max_age`` are dropped instead of arriving days late.
* If the outbox is unavailable (database down), the alert is sent straight from memory.
* On shutdown, in-flight deliveries get a grace period; unfinished ones stay pending.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from datetime import datetime, timedelta
from typing import Any

from structlog.stdlib import BoundLogger

from vision_hub.core.logging import get_logger
from vision_hub.core.metrics import Metrics
from vision_hub.core.security import utc_now
from vision_hub.core.tasks import TaskSupervisor
from vision_hub.domain.bus import EventBus, Subscription
from vision_hub.domain.events import MOTION_ENDED, CameraEvent, MotionEndedEvent
from vision_hub.domain.motion import MotionEvent
from vision_hub.domain.notifications import (
    Alert,
    Delivery,
    NotificationError,
    NotificationOutbox,
    Notifier,
    RetryPolicy,
)

logger = get_logger(__name__)

type DeviceNames = Callable[[str], str]
type EvidenceLoader = Callable[[str], Awaitable[tuple[MotionEvent, bytes] | None]]


class NotificationService:
    BATCH = 50

    def __init__(
        self,
        bus: EventBus[CameraEvent],
        notifiers: Sequence[Notifier],
        *,
        outbox: NotificationOutbox,
        evidence: EvidenceLoader,
        device_name: DeviceNames,
        cooldown_seconds: float,
        policy: RetryPolicy | None = None,
        tasks: TaskSupervisor | None = None,
        metrics: Metrics | None = None,
        clock: Callable[[], datetime] = utc_now,
        poll_interval: float = 5.0,
        max_concurrent: int = 4,
    ) -> None:
        self._bus = bus
        self._notifiers = {notifier.name: notifier for notifier in notifiers}
        self._outbox = outbox
        self._evidence = evidence
        self._device_name = device_name
        self._cooldown = timedelta(seconds=cooldown_seconds)
        self._policy = policy or RetryPolicy()
        self._tasks = tasks or TaskSupervisor()
        self._metrics = metrics or Metrics(process_metrics=False)
        self._clock = clock
        self._poll_interval = poll_interval
        self._slots = asyncio.Semaphore(max_concurrent)
        self._last_alert: dict[str, datetime] = {}
        self._wake = asyncio.Event()
        self._in_flight: set[str] = set()  # delivery ids being attempted right now
        self._deliveries: set[asyncio.Task[None]] = set()
        self._subscription: Subscription[CameraEvent] | None = None
        self._consumer: asyncio.Task[None] | None = None
        self._dispatcher: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._notifiers)

    async def start(self) -> None:
        if self._consumer is not None or not self.enabled:
            return
        # Subscribe first, before cameras start, so no event published afterwards is missed.
        subscription = self._subscription = self._bus.subscribe(f"{MOTION_ENDED}.*")
        pending: int | None = None
        try:
            since = self._clock() - self._cooldown
            self._last_alert.update(await self._outbox.last_alerts(since=since))
            pending = await self._outbox.pending_count()
        except Exception:
            logger.exception("outbox_unavailable")
        self._consumer = self._tasks.spawn(
            "notifications-consumer", lambda: self._consume(subscription)
        )
        self._dispatcher = self._tasks.spawn("notifications-dispatcher", self._dispatch_loop)
        # Pending deliveries left by the previous run are picked up by the first dispatch.
        logger.info("notifications_started", channels=list(self._notifiers), pending=pending)

    async def stop(self, drain_timeout: float = 10.0) -> None:
        """Store alerts for events already queued (e.g. ones closed as cameras stopped), give
        them a first attempt, then give in-flight deliveries ``drain_timeout`` seconds."""
        if not self.enabled:
            return
        if self._subscription is not None:
            self._subscription.close()  # the consumer ends after the queued events
        if self._consumer is not None:
            await self._consumer
        if self._dispatcher is not None:
            self._dispatcher.cancel()
            await asyncio.gather(self._dispatcher, return_exceptions=True)
        self._subscription = self._consumer = self._dispatcher = None
        try:
            await self._dispatch_due()
        except Exception:
            logger.exception("outbox_unavailable")
        if self._deliveries:
            _, unfinished = await asyncio.wait(self._deliveries, timeout=drain_timeout)
            for task in unfinished:
                task.cancel()
            await asyncio.gather(*unfinished, return_exceptions=True)
            if unfinished:
                # Outbox deliveries stay pending and are retried after the restart.
                logger.warning("notifications_interrupted_by_shutdown", count=len(unfinished))

    async def handle(self, ended: MotionEndedEvent) -> None:
        device_id = ended.event.device_id
        if not ended.event.alert:
            # The camera alerts on people only and none was seen. Before the cooldown, so a
            # quiet event never holds back the alert for a real one.
            logger.info("alert_skipped_no_person", device_id=device_id, event_id=ended.event.id)
            return
        now = self._clock()
        last = self._last_alert.get(device_id)
        if last is not None and now - last < self._cooldown:
            self._metrics.alerts_suppressed.labels(device_id).inc()
            logger.info(
                "alert_suppressed_by_cooldown", device_id=device_id, event_id=ended.event.id
            )
            return
        self._last_alert[device_id] = now
        channels = [name for name, notifier in self._notifiers.items() if await _wanted(notifier)]
        if not channels:
            return
        try:
            await self._outbox.enqueue(ended.event.id, device_id, channels, at=now)
        except Exception:
            logger.exception("outbox_unavailable", device_id=device_id, event_id=ended.event.id)
            alert = self._alert(ended.event, ended.annotated_jpeg)
            for notifier in (self._notifiers[name] for name in channels):
                self._track(self._send_directly(notifier, alert))
            return
        self._wake.set()

    async def _consume(self, subscription: Subscription[CameraEvent]) -> None:
        async for envelope in subscription:
            if isinstance(envelope.message, MotionEndedEvent):
                await self.handle(envelope.message)

    async def _dispatch_loop(self) -> None:
        while True:
            self._wake.clear()
            await self._dispatch_due()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self._poll_interval):
                    await self._wake.wait()

    async def _dispatch_due(self) -> None:
        due = await self._outbox.due(
            self._clock(), channels=list(self._notifiers), limit=self.BATCH
        )
        for delivery in due:
            if delivery.id not in self._in_flight:
                self._in_flight.add(delivery.id)
                self._track(self._attempt(delivery))

    async def _attempt(self, delivery: Delivery) -> None:
        log = logger.bind(
            channel=delivery.channel, device_id=delivery.device_id, event_id=delivery.event_id
        )
        try:
            async with self._slots:
                await self._try(delivery, log)
        except Exception:
            # The outbox could not be read or updated: the delivery stays pending.
            log.exception("alert_dispatch_failed")
        finally:
            self._in_flight.discard(delivery.id)

    async def _try(self, delivery: Delivery, log: BoundLogger) -> None:
        now = self._clock()
        if now - delivery.created_at > self._policy.max_age:
            await self._outbox.mark_failed(
                delivery.id, attempts=delivery.attempts, at=now, error="expired"
            )
            self._metrics.alerts.labels(delivery.channel, "expired").inc()
            log.warning("alert_expired", age_s=round((now - delivery.created_at).total_seconds()))
            return
        evidence = await self._evidence(delivery.event_id)
        if evidence is None:  # deleted by retention between the query and now
            await self._outbox.mark_failed(
                delivery.id, attempts=delivery.attempts, at=now, error="event no longer exists"
            )
            log.warning("alert_event_missing")
            return

        attempts = delivery.attempts + 1
        try:
            with self._metrics.alert_delivery_seconds.labels(delivery.channel).time():
                await self._notifiers[delivery.channel].send(self._alert(*evidence))
        except Exception as exc:
            error = (
                exc
                if isinstance(exc, NotificationError)
                else NotificationError(f"unexpected error: {exc!r}", retryable=True)
            )
            await self._failed(delivery, attempts, error, log)
            return
        await self._outbox.mark_sent(delivery.id, attempts=attempts, at=self._clock())
        self._metrics.alerts.labels(delivery.channel, "sent").inc()
        log.info("alert_sent", attempts=attempts)

    async def _failed(
        self,
        delivery: Delivery,
        attempts: int,
        error: NotificationError,
        log: BoundLogger,
    ) -> None:
        now = self._clock()
        if not error.retryable or attempts >= self._policy.max_attempts:
            await self._outbox.mark_failed(delivery.id, attempts=attempts, at=now, error=str(error))
            self._metrics.alerts.labels(delivery.channel, "failed").inc()
            log.error(
                "alert_failed", attempts=attempts, error=str(error), retryable=error.retryable
            )
            return
        delay = self._policy.delay(attempts)
        await self._outbox.retry_later(
            delivery.id, attempts=attempts, next_attempt_at=now + delay, error=str(error)
        )
        self._metrics.alerts.labels(delivery.channel, "retry").inc()
        log.warning(
            "alert_retry",
            attempt=attempts,
            delay_s=round(delay.total_seconds(), 1),
            error=str(error),
        )

    async def _send_directly(self, notifier: Notifier, alert: Alert) -> None:
        """Fallback without the outbox: retries happen in memory and end with the process."""
        log = logger.bind(channel=notifier.name, device_id=alert.device_id, event_id=alert.event.id)
        attempt = 0
        while True:
            attempt += 1
            try:
                await notifier.send(alert)
            except NotificationError as exc:
                if not exc.retryable or attempt == self._policy.max_attempts:
                    self._metrics.alerts.labels(notifier.name, "failed").inc()
                    log.error(
                        "alert_failed", attempts=attempt, error=str(exc), retryable=exc.retryable
                    )
                    return
                self._metrics.alerts.labels(notifier.name, "retry").inc()
                await asyncio.sleep(self._policy.delay(attempt).total_seconds())
            else:
                self._metrics.alerts.labels(notifier.name, "sent").inc()
                log.info("alert_sent", attempts=attempt, outbox=False)
                return

    async def refresh_metrics(self) -> None:
        """Before a scrape: how many alerts wait in the outbox."""
        if self.enabled:
            self._metrics.alerts_pending.set(await self._outbox.pending_count())

    def _alert(self, event: MotionEvent, image_jpeg: bytes) -> Alert:
        return Alert(
            device_id=event.device_id,
            device_name=self._device_name(event.device_id),
            event=event,
            image_jpeg=image_jpeg,
        )

    def _track(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._deliveries.add(task)
        task.add_done_callback(self._deliveries.discard)


async def _wanted(notifier: Notifier) -> bool:
    """Whether to store a delivery for this channel. When unsure (its storage is down), yes."""
    try:
        return await notifier.has_recipients()
    except Exception:
        logger.exception("notifier_recipients_unknown", channel=notifier.name)
        return True
