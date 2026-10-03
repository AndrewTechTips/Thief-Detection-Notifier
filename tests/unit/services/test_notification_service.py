import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from vision_hub.domain.events import (
    CameraEvent,
    MotionEndedEvent,
    MotionStartedEvent,
    topic_for,
)
from vision_hub.domain.motion import MotionEvent
from vision_hub.domain.notifications import Alert, NotificationError
from vision_hub.infra.bus.memory import InMemoryEventBus
from vision_hub.services.notifications import NotificationService
from vision_hub.vision.sources import Backoff

type LogRecords = Callable[[], list[dict[str, Any]]]
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def ended(device_id: str = "porch", event_id: str = "e1") -> MotionEndedEvent:
    event = MotionEvent(id=event_id, device_id=device_id, started_at=T0, ended_at=T0)
    return MotionEndedEvent(event=event, snapshot_jpeg=b"clean", annotated_jpeg=b"boxes")


class FakeNotifier:
    def __init__(
        self,
        name: str = "fake",
        *,
        failures: list[NotificationError] | None = None,
        delay: float = 0,
    ) -> None:
        self._name = name
        self.failures = list(failures or [])
        self.delay = delay
        self.attempts = 0
        self.sent: list[Alert] = []
        self.delivered = asyncio.Event()

    @property
    def name(self) -> str:
        return self._name

    async def send(self, alert: Alert) -> None:
        self.attempts += 1
        await asyncio.sleep(self.delay)
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append(alert)
        self.delivered.set()


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def service(
    bus: InMemoryEventBus[CameraEvent], *notifiers: FakeNotifier, clock: Clock | None = None
) -> NotificationService:
    return NotificationService(
        bus,
        notifiers,
        device_name=lambda device_id: {"porch": "Front porch"}.get(device_id, device_id),
        cooldown_seconds=60,
        backoff=lambda: Backoff(initial=0.001, maximum=0.001),
        clock=clock or Clock(),
    )


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0.01)


@pytest.fixture
def bus() -> InMemoryEventBus[CameraEvent]:
    return InMemoryEventBus()


def publish(bus: InMemoryEventBus[CameraEvent], event: CameraEvent) -> None:
    bus.publish(topic_for(event), event)


class TestAlerts:
    async def test_finished_motion_becomes_an_alert(
        self, bus: InMemoryEventBus[CameraEvent]
    ) -> None:
        notifier = FakeNotifier()
        alerts = service(bus, notifier)
        alerts.start()

        publish(bus, ended())
        await asyncio.wait_for(notifier.delivered.wait(), 1)
        await alerts.stop()

        [alert] = notifier.sent
        assert (alert.device_id, alert.device_name) == ("porch", "Front porch")
        assert alert.image_jpeg == b"boxes"  # the annotated frame

    async def test_motion_start_does_not_alert(self, bus: InMemoryEventBus[CameraEvent]) -> None:
        notifier = FakeNotifier()
        alerts = service(bus, notifier)
        alerts.start()

        publish(bus, MotionStartedEvent(event=ended().event))
        await settle()
        await alerts.stop()

        assert notifier.sent == []

    async def test_every_channel_receives_the_alert(
        self, bus: InMemoryEventBus[CameraEvent]
    ) -> None:
        email, webhook = FakeNotifier("email"), FakeNotifier("webhook")
        alerts = service(bus, email, webhook)
        alerts.start()

        publish(bus, ended())
        await alerts.stop()

        assert len(email.sent) == len(webhook.sent) == 1

    async def test_other_messages_on_the_topic_are_ignored(
        self, bus: InMemoryEventBus[CameraEvent]
    ) -> None:
        notifier = FakeNotifier()
        alerts = service(bus, notifier)
        alerts.start()

        bus.publish("motion.ended.porch", MotionStartedEvent(event=ended().event))
        await alerts.stop()

        assert notifier.sent == []

    def test_needs_at_least_one_attempt(self, bus: InMemoryEventBus[CameraEvent]) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            NotificationService(bus, [], device_name=str, cooldown_seconds=0, max_attempts=0)

    async def test_disabled_without_channels(self, bus: InMemoryEventBus[CameraEvent]) -> None:
        alerts = service(bus)

        alerts.start()
        await alerts.stop()

        assert alerts.enabled is False
        assert bus.subscriber_count == 0


class TestCooldown:
    async def test_suppresses_repeat_alerts_per_device(
        self, bus: InMemoryEventBus[CameraEvent], log_records: LogRecords
    ) -> None:
        clock = Clock()
        notifier = FakeNotifier()
        alerts = service(bus, notifier, clock=clock)

        alerts.handle(ended("porch", "e1"))
        clock.now += timedelta(seconds=30)
        alerts.handle(ended("porch", "e2"))  # within 60 s: suppressed
        alerts.handle(ended("gate", "e3"))  # other device: not affected
        clock.now += timedelta(seconds=31)
        alerts.handle(ended("porch", "e4"))  # cooldown over
        await alerts.stop()

        assert [a.event.id for a in notifier.sent] == ["e1", "e3", "e4"]
        assert any(r["event"] == "alert_suppressed_by_cooldown" for r in log_records())


class TestRetries:
    async def test_transient_failures_are_retried(
        self, bus: InMemoryEventBus[CameraEvent], log_records: LogRecords
    ) -> None:
        notifier = FakeNotifier(failures=[NotificationError("timeout", retryable=True)] * 2)
        alerts = service(bus, notifier)

        alerts.handle(ended())
        await alerts.stop()

        assert notifier.attempts == 3
        assert len(notifier.sent) == 1
        sent = next(r for r in log_records() if r["event"] == "alert_sent")
        assert sent["attempts"] == 3

    async def test_permanent_failures_are_not_retried(
        self, bus: InMemoryEventBus[CameraEvent], log_records: LogRecords
    ) -> None:
        notifier = FakeNotifier(failures=[NotificationError("bad login", retryable=False)])
        alerts = service(bus, notifier)

        alerts.handle(ended())
        await alerts.stop()

        assert notifier.attempts == 1
        failed = next(r for r in log_records() if r["event"] == "alert_failed")
        assert (failed["retryable"], failed["level"]) == (False, "error")

    async def test_gives_up_after_max_attempts(self, bus: InMemoryEventBus[CameraEvent]) -> None:
        notifier = FakeNotifier(failures=[NotificationError("down", retryable=True)] * 10)
        alerts = service(bus, notifier)

        alerts.handle(ended())
        await alerts.stop()

        assert notifier.attempts == 3
        assert notifier.sent == []


class TestConcurrencyAndShutdown:
    async def test_slow_channel_does_not_delay_others(
        self, bus: InMemoryEventBus[CameraEvent]
    ) -> None:
        slow, fast = FakeNotifier("slow", delay=1), FakeNotifier("fast")
        alerts = service(bus, slow, fast)
        alerts.start()

        publish(bus, ended())
        await asyncio.wait_for(fast.delivered.wait(), 0.5)

        assert slow.sent == []
        await alerts.stop()
        assert len(slow.sent) == 1  # drained on shutdown

    async def test_stop_handles_already_queued_events(
        self, bus: InMemoryEventBus[CameraEvent]
    ) -> None:
        notifier = FakeNotifier()
        alerts = service(bus, notifier)
        alerts.start()

        publish(bus, ended("porch"))
        publish(bus, ended("gate"))
        await alerts.stop()  # without yielding first: both are still queued

        assert {a.device_id for a in notifier.sent} == {"porch", "gate"}

    async def test_hanging_delivery_is_abandoned_after_the_drain_timeout(
        self, bus: InMemoryEventBus[CameraEvent], log_records: LogRecords
    ) -> None:
        notifier = FakeNotifier(delay=30)
        alerts = service(bus, notifier)
        alerts.handle(ended())

        await alerts.stop(drain_timeout=0.05)

        assert notifier.sent == []
        assert any(r["event"] == "notifications_abandoned_on_shutdown" for r in log_records())
