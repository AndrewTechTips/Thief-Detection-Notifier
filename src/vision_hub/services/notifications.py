"""Turns finished motion events into alerts on every configured channel.

* per-device cooldown, so one busy camera cannot flood an inbox (events themselves are never
  dropped; only the alert is suppressed);
* one task per delivery, so a slow SMTP server never delays handling the next event;
* transient failures retried with backoff; permanent ones (bad credentials) are not;
* on shutdown, in-flight deliveries get a grace period to finish.
"""

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from vision_hub.core.logging import get_logger
from vision_hub.core.security import utc_now
from vision_hub.domain.bus import EventBus, Subscription
from vision_hub.domain.events import MOTION_ENDED, CameraEvent, MotionEndedEvent
from vision_hub.domain.notifications import Alert, NotificationError, Notifier
from vision_hub.vision.sources import Backoff

logger = get_logger(__name__)

type DeviceNames = Callable[[str], str]


class NotificationService:
    def __init__(
        self,
        bus: EventBus[CameraEvent],
        notifiers: Sequence[Notifier],
        *,
        device_name: DeviceNames,
        cooldown_seconds: float,
        max_attempts: int = 3,
        backoff: Callable[[], Backoff] = lambda: Backoff(initial=2, maximum=30),
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._bus = bus
        self._notifiers = tuple(notifiers)
        self._device_name = device_name
        self._cooldown = timedelta(seconds=cooldown_seconds)
        if max_attempts < 1:
            msg = "max_attempts must be at least 1"
            raise ValueError(msg)
        self._max_attempts = max_attempts
        self._backoff = backoff
        self._clock = clock
        self._last_alert: dict[str, datetime] = {}
        self._subscription: Subscription[CameraEvent] | None = None
        self._consumer: asyncio.Task[None] | None = None
        self._deliveries: set[asyncio.Task[None]] = set()

    @property
    def enabled(self) -> bool:
        return bool(self._notifiers)

    def start(self) -> None:
        if self._consumer is not None or not self.enabled:
            return
        # Subscribe now, before cameras start, so no event published afterwards is missed.
        self._subscription = self._bus.subscribe(f"{MOTION_ENDED}.*")
        self._consumer = asyncio.create_task(
            self._consume(self._subscription), name="notifications"
        )
        logger.info("notifications_started", channels=[n.name for n in self._notifiers])

    async def stop(self, drain_timeout: float = 10.0) -> None:
        """Handle events already queued (e.g. ones closed as cameras stopped), then give
        in-flight deliveries ``drain_timeout`` seconds to finish."""
        if self._subscription is not None and self._consumer is not None:
            # The consumer never blocks (handle() only schedules deliveries), so once the
            # subscription is closed it finishes right after the queued events.
            self._subscription.close()
            await self._consumer
            self._subscription = self._consumer = None
        if self._deliveries:
            _, pending = await asyncio.wait(self._deliveries, timeout=drain_timeout)
            for task in pending:
                task.cancel()
            if pending:
                logger.warning("notifications_abandoned_on_shutdown", count=len(pending))

    async def _consume(self, subscription: Subscription[CameraEvent]) -> None:
        async with subscription:
            async for envelope in subscription:
                if isinstance(envelope.message, MotionEndedEvent):
                    self.handle(envelope.message)

    def handle(self, ended: MotionEndedEvent) -> None:
        device_id = ended.event.device_id
        now = self._clock()
        last = self._last_alert.get(device_id)
        if last is not None and now - last < self._cooldown:
            logger.info(
                "alert_suppressed_by_cooldown", device_id=device_id, event_id=ended.event.id
            )
            return
        self._last_alert[device_id] = now

        alert = Alert(
            device_id=device_id,
            device_name=self._device_name(device_id),
            event=ended.event,
            image_jpeg=ended.annotated_jpeg,
        )
        for notifier in self._notifiers:
            task = asyncio.create_task(self._deliver(notifier, alert))
            self._deliveries.add(task)
            task.add_done_callback(self._deliveries.discard)

    async def _deliver(self, notifier: Notifier, alert: Alert) -> None:
        backoff = self._backoff()
        log = logger.bind(channel=notifier.name, device_id=alert.device_id, event_id=alert.event.id)
        attempt = 0
        while True:
            attempt += 1
            try:
                await notifier.send(alert)
            except NotificationError as exc:
                if not exc.retryable or attempt == self._max_attempts:
                    log.error(
                        "alert_failed", attempts=attempt, error=str(exc), retryable=exc.retryable
                    )
                    return
                delay = backoff.next_delay()
                log.warning("alert_retry", attempt=attempt, delay_s=round(delay, 1), error=str(exc))
                await asyncio.sleep(delay)
                continue
            log.info("alert_sent", attempts=attempt)
            return
