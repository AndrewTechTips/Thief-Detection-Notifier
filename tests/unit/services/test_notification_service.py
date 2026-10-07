import asyncio
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from vision_hub.core.metrics import Metrics
from vision_hub.core.security import utc_now
from vision_hub.domain.events import (
    CameraEvent,
    MotionEndedEvent,
    MotionStartedEvent,
    topic_for,
)
from vision_hub.domain.motion import MotionEvent
from vision_hub.domain.notifications import (
    Alert,
    Delivery,
    DeliveryStatus,
    NotificationError,
    RetryPolicy,
)
from vision_hub.infra.bus.memory import InMemoryEventBus
from vision_hub.services.notifications import NotificationService

type LogRecords = Callable[[], list[dict[str, Any]]]
T0 = datetime(2026, 1, 1, tzinfo=UTC)
FAST = RetryPolicy(
    max_attempts=3, initial_delay=timedelta(milliseconds=1), max_delay=timedelta(milliseconds=1)
)


class FakeNotifier:
    def __init__(
        self,
        name: str = "fake",
        *,
        failures: Sequence[Exception] = (),
        delay: float = 0,
        recipients: bool | Exception = True,
    ) -> None:
        self._name = name
        self.recipients = recipients
        self.failures = list(failures)
        self.delay = delay
        self.attempts = 0
        self.sent: list[Alert] = []
        self.delivered = asyncio.Event()

    @property
    def name(self) -> str:
        return self._name

    async def has_recipients(self) -> bool:
        if isinstance(self.recipients, Exception):
            raise self.recipients
        return self.recipients

    async def send(self, alert: Alert) -> None:
        self.attempts += 1
        await asyncio.sleep(self.delay)
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append(alert)
        self.delivered.set()


@dataclass
class Row:
    id: str
    event_id: str
    device_id: str
    channel: str
    created_at: datetime
    next_attempt_at: datetime
    status: DeliveryStatus = DeliveryStatus.PENDING
    attempts: int = 0
    last_error: str | None = None


class MemoryOutbox:
    def __init__(self) -> None:
        self.rows: dict[str, Row] = {}
        self.broken = False

    async def enqueue(
        self, event_id: str, device_id: str, channels: Sequence[str], *, at: datetime
    ) -> None:
        self._check()
        for channel in channels:
            row_id = f"{event_id}:{channel}"
            self.rows.setdefault(row_id, Row(row_id, event_id, device_id, channel, at, at))

    async def due(
        self, now: datetime, *, channels: Collection[str], limit: int
    ) -> Sequence[Delivery]:
        self._check()
        rows = sorted(
            (
                row
                for row in self.rows.values()
                if row.status is DeliveryStatus.PENDING
                and row.next_attempt_at <= now
                and row.channel in channels
            ),
            key=lambda row: row.next_attempt_at,
        )
        return [
            Delivery(r.id, r.event_id, r.device_id, r.channel, r.attempts, r.created_at)
            for r in rows[:limit]
        ]

    async def mark_sent(self, delivery_id: str, *, attempts: int, at: datetime) -> None:
        self._check()
        self._set(delivery_id, status=DeliveryStatus.SENT, attempts=attempts)

    async def mark_failed(
        self, delivery_id: str, *, attempts: int, at: datetime, error: str
    ) -> None:
        self._set(delivery_id, status=DeliveryStatus.FAILED, attempts=attempts, last_error=error)

    async def retry_later(
        self, delivery_id: str, *, attempts: int, next_attempt_at: datetime, error: str
    ) -> None:
        self._set(delivery_id, attempts=attempts, next_attempt_at=next_attempt_at, last_error=error)

    async def pending_count(self) -> int:
        self._check()
        return sum(row.status is DeliveryStatus.PENDING for row in self.rows.values())

    async def last_alerts(self, *, since: datetime) -> Mapping[str, datetime]:
        self._check()
        last: dict[str, datetime] = {}
        for row in self.rows.values():
            if row.created_at >= since:
                last[row.device_id] = max(row.created_at, last.get(row.device_id, row.created_at))
        return last

    def statuses(self) -> list[tuple[DeliveryStatus, int]]:
        return [(row.status, row.attempts) for row in self.rows.values()]

    def _set(self, delivery_id: str, **values: Any) -> None:
        row = self.rows[delivery_id]
        for key, value in values.items():
            setattr(row, key, value)

    def _check(self) -> None:
        if self.broken:
            raise ConnectionError("database down")


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


class Harness:
    """A bus, an outbox and the stored events the service loads evidence from."""

    def __init__(self) -> None:
        self.bus: InMemoryEventBus[CameraEvent] = InMemoryEventBus()
        self.outbox = MemoryOutbox()
        self.events: dict[str, MotionEvent] = {}

    def ended(
        self, device_id: str = "porch", event_id: str = "e1", *, alert: bool = True
    ) -> MotionEndedEvent:
        event = MotionEvent(
            id=event_id, device_id=device_id, started_at=T0, ended_at=T0, alert=alert
        )
        self.events[event_id] = event
        return MotionEndedEvent(event=event, snapshot_jpeg=b"clean", annotated_jpeg=b"in-memory")

    async def evidence(self, event_id: str) -> tuple[MotionEvent, bytes] | None:
        event = self.events.get(event_id)
        return (event, b"stored") if event else None

    def service(
        self,
        *notifiers: FakeNotifier,
        clock: Callable[[], datetime] = utc_now,
        policy: RetryPolicy = FAST,
        cooldown_seconds: float = 60,
        metrics: Metrics | None = None,
    ) -> NotificationService:
        return NotificationService(
            self.bus,
            notifiers,
            outbox=self.outbox,
            evidence=self.evidence,
            device_name=lambda device_id: {"porch": "Front porch"}.get(device_id, device_id),
            cooldown_seconds=cooldown_seconds,
            policy=policy,
            clock=clock,
            poll_interval=0.01,
            metrics=metrics,
        )

    def publish(self, event: CameraEvent) -> None:
        self.bus.publish(topic_for(event), event)


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0.01)


async def eventually(condition: Callable[[], bool], within: float = 2) -> None:
    async with asyncio.timeout(within):
        while not condition():
            await asyncio.sleep(0.01)


@pytest.fixture
def hub() -> Harness:
    return Harness()


class TestAlerts:
    async def test_finished_motion_becomes_an_alert(self, hub: Harness) -> None:
        notifier = FakeNotifier()
        alerts = hub.service(notifier)
        await alerts.start()

        hub.publish(hub.ended())
        await asyncio.wait_for(notifier.delivered.wait(), 1)
        await alerts.stop()

        [alert] = notifier.sent
        assert (alert.device_id, alert.device_name) == ("porch", "Front porch")
        assert alert.image_jpeg == b"stored"  # loaded like after a restart: one code path

    async def test_motion_start_does_not_alert(self, hub: Harness) -> None:
        notifier = FakeNotifier()
        alerts = hub.service(notifier)
        await alerts.start()

        hub.publish(MotionStartedEvent(event=hub.ended().event))
        await settle()
        await alerts.stop()

        assert notifier.sent == []
        assert hub.outbox.rows == {}

    async def test_every_channel_receives_the_alert(self, hub: Harness) -> None:
        email, webhook = FakeNotifier("email"), FakeNotifier("webhook")
        alerts = hub.service(email, webhook)
        await alerts.start()

        hub.publish(hub.ended())
        await alerts.stop()

        assert len(email.sent) == len(webhook.sent) == 1
        assert sorted(hub.outbox.rows) == ["e1:email", "e1:webhook"]

    async def test_channels_nobody_listens_on_are_skipped(self, hub: Harness) -> None:
        email, push = FakeNotifier("email"), FakeNotifier("push", recipients=False)
        alerts = hub.service(email, push)
        await alerts.start()

        hub.publish(hub.ended())
        await alerts.stop()

        assert push.attempts == 0
        assert sorted(hub.outbox.rows) == ["e1:email"]

    async def test_a_channel_that_cannot_tell_still_gets_the_alert(
        self, hub: Harness, log_records: LogRecords
    ) -> None:
        push = FakeNotifier("push", recipients=RuntimeError("database down"))
        alerts = hub.service(push)

        await alerts.handle(hub.ended())
        await alerts.stop()

        assert len(push.sent) == 1
        assert any(r["event"] == "notifier_recipients_unknown" for r in log_records())

    async def test_events_that_do_not_alert_are_skipped_without_using_the_cooldown(
        self, hub: Harness
    ) -> None:
        # A people-only camera: the first event had nobody in it, the second had someone.
        notifier = FakeNotifier()
        alerts = hub.service(notifier, cooldown_seconds=3600)

        await alerts.handle(hub.ended(event_id="e1", alert=False))
        await alerts.handle(hub.ended(event_id="e2"))
        await alerts.stop()

        assert [a.event.id for a in notifier.sent] == ["e2"]
        assert sorted(hub.outbox.rows) == ["e2:fake"]

    async def test_other_messages_on_the_topic_are_ignored(self, hub: Harness) -> None:
        notifier = FakeNotifier()
        alerts = hub.service(notifier)
        await alerts.start()

        hub.bus.publish("motion.ended.porch", MotionStartedEvent(event=hub.ended().event))
        await alerts.stop()

        assert notifier.sent == []

    async def test_disabled_without_channels(self, hub: Harness) -> None:
        alerts = hub.service()

        await alerts.start()
        await alerts.stop()

        assert alerts.enabled is False
        assert hub.bus.subscriber_count == 0

    async def test_starting_twice_subscribes_once(self, hub: Harness) -> None:
        alerts = hub.service(FakeNotifier())

        await alerts.start()
        await alerts.start()

        assert hub.bus.subscriber_count == 1
        await alerts.stop()


class TestOutbox:
    async def test_alerts_are_stored_before_they_are_sent(self, hub: Harness) -> None:
        notifier = FakeNotifier()
        alerts = hub.service(notifier)

        await alerts.handle(hub.ended())

        assert hub.outbox.statuses() == [(DeliveryStatus.PENDING, 0)]
        await alerts.stop()
        assert hub.outbox.statuses() == [(DeliveryStatus.SENT, 1)]

    async def test_deliveries_left_by_a_previous_run_are_sent_on_start(
        self, hub: Harness, log_records: LogRecords
    ) -> None:
        hub.ended("porch", "old")
        await hub.outbox.enqueue("old", "porch", ["fake"], at=utc_now())
        notifier = FakeNotifier()
        alerts = hub.service(notifier)

        await alerts.start()
        await asyncio.wait_for(notifier.delivered.wait(), 1)
        await alerts.stop()

        assert notifier.sent[0].event.id == "old"
        started = next(r for r in log_records() if r["event"] == "notifications_started")
        assert started["pending"] == 1

    async def test_cooldown_survives_a_restart(self, hub: Harness) -> None:
        clock = Clock()
        await hub.outbox.enqueue("earlier", "porch", ["fake"], at=clock.now)
        hub.outbox.rows["earlier:fake"].status = DeliveryStatus.SENT
        notifier = FakeNotifier()
        alerts = hub.service(notifier, clock=clock)
        await alerts.start()

        clock.now += timedelta(seconds=30)
        await alerts.handle(hub.ended("porch", "e2"))
        await alerts.stop()

        assert notifier.sent == []  # within the cooldown of the alert sent before the restart

    async def test_without_the_outbox_alerts_are_sent_from_memory(
        self, hub: Harness, log_records: LogRecords
    ) -> None:
        hub.outbox.broken = True
        notifier = FakeNotifier(failures=[NotificationError("busy", retryable=True)])
        alerts = hub.service(notifier)
        await alerts.start()  # also tolerates the outbox being down

        hub.publish(hub.ended())
        await asyncio.wait_for(notifier.delivered.wait(), 1)
        await alerts.stop()

        assert notifier.sent[0].image_jpeg == b"in-memory"
        events = [r["event"] for r in log_records()]
        assert "outbox_unavailable" in events
        sent = next(r for r in log_records() if r["event"] == "alert_sent")
        assert (sent["outbox"], sent["attempts"]) == (False, 2)

    async def test_direct_sending_gives_up_like_the_outbox(self, hub: Harness) -> None:
        hub.outbox.broken = True
        bad_login = FakeNotifier("a", failures=[NotificationError("auth", retryable=False)])
        down = FakeNotifier("b", failures=[NotificationError("down", retryable=True)] * 5)
        alerts = hub.service(bad_login, down)

        await alerts.handle(hub.ended())
        await alerts.stop()

        assert (bad_login.attempts, down.attempts) == (1, 3)

    async def test_stale_alerts_are_dropped(self, hub: Harness, log_records: LogRecords) -> None:
        hub.ended("porch", "old")
        await hub.outbox.enqueue("old", "porch", ["fake"], at=utc_now() - timedelta(days=2))
        notifier = FakeNotifier()
        alerts = hub.service(notifier)

        await alerts.stop()

        assert notifier.attempts == 0
        assert hub.outbox.rows["old:fake"].last_error == "expired"
        assert any(r["event"] == "alert_expired" for r in log_records())

    async def test_alerts_for_deleted_events_are_dropped(self, hub: Harness) -> None:
        await hub.outbox.enqueue("gone", "porch", ["fake"], at=utc_now())
        notifier = FakeNotifier()

        await hub.service(notifier).stop()

        assert notifier.attempts == 0
        assert hub.outbox.statuses() == [(DeliveryStatus.FAILED, 0)]

    async def test_outbox_errors_leave_the_delivery_pending(
        self, hub: Harness, log_records: LogRecords
    ) -> None:
        notifier = FakeNotifier()
        alerts = hub.service(notifier)
        await alerts.handle(hub.ended())

        async def broken(*_: object, **__: object) -> None:
            raise ConnectionError("database down")

        hub.outbox.mark_sent = broken  # type: ignore[method-assign]
        await alerts.stop()

        assert len(notifier.sent) == 1
        assert hub.outbox.statuses() == [(DeliveryStatus.PENDING, 0)]  # sent again later
        assert any(r["event"] == "alert_dispatch_failed" for r in log_records())

    async def test_shutdown_survives_an_unavailable_outbox(
        self, hub: Harness, log_records: LogRecords
    ) -> None:
        alerts = hub.service(FakeNotifier())
        await alerts.start()
        hub.outbox.broken = True

        await alerts.stop()

        assert "outbox_unavailable" in [r["event"] for r in log_records()]


class TestCooldown:
    async def test_suppresses_repeat_alerts_per_device(
        self, hub: Harness, log_records: LogRecords
    ) -> None:
        clock = Clock()
        notifier = FakeNotifier()
        alerts = hub.service(notifier, clock=clock)

        await alerts.handle(hub.ended("porch", "e1"))
        clock.now += timedelta(seconds=30)
        await alerts.handle(hub.ended("porch", "e2"))  # within 60 s: suppressed
        await alerts.handle(hub.ended("gate", "e3"))  # other device: not affected
        clock.now += timedelta(seconds=31)
        await alerts.handle(hub.ended("porch", "e4"))  # cooldown over
        await alerts.stop()

        assert sorted(a.event.id for a in notifier.sent) == ["e1", "e3", "e4"]
        assert any(r["event"] == "alert_suppressed_by_cooldown" for r in log_records())


class TestRetries:
    async def test_transient_failures_are_retried(
        self, hub: Harness, log_records: LogRecords
    ) -> None:
        notifier = FakeNotifier(failures=[NotificationError("timeout", retryable=True)] * 2)
        alerts = hub.service(notifier)
        await alerts.start()

        hub.publish(hub.ended())
        await asyncio.wait_for(notifier.delivered.wait(), 2)
        await alerts.stop()

        assert notifier.attempts == 3
        assert hub.outbox.statuses() == [(DeliveryStatus.SENT, 3)]
        sent = next(r for r in log_records() if r["event"] == "alert_sent")
        assert sent["attempts"] == 3
        assert hub.outbox.rows["e1:fake"].last_error == "timeout"

    async def test_permanent_failures_are_not_retried(
        self, hub: Harness, log_records: LogRecords
    ) -> None:
        notifier = FakeNotifier(failures=[NotificationError("bad login", retryable=False)])
        alerts = hub.service(notifier)

        await alerts.handle(hub.ended())
        await alerts.stop()

        assert notifier.attempts == 1
        assert hub.outbox.statuses() == [(DeliveryStatus.FAILED, 1)]
        failed = next(r for r in log_records() if r["event"] == "alert_failed")
        assert (failed["retryable"], failed["level"]) == (False, "error")

    async def test_gives_up_after_max_attempts(self, hub: Harness) -> None:
        notifier = FakeNotifier(failures=[NotificationError("down", retryable=True)] * 10)
        alerts = hub.service(notifier)
        await alerts.start()

        hub.publish(hub.ended())
        await eventually(lambda: hub.outbox.statuses() == [(DeliveryStatus.FAILED, 3)])
        await alerts.stop()

        assert notifier.attempts == 3
        assert notifier.sent == []

    async def test_unexpected_errors_count_as_transient_failures(self, hub: Harness) -> None:
        notifier = FakeNotifier(failures=[RuntimeError("bug in a channel")])
        alerts = hub.service(notifier)
        await alerts.handle(hub.ended())

        await alerts.stop()  # the first attempt fails and is rescheduled

        [row] = hub.outbox.rows.values()
        assert (row.status, row.attempts) == (DeliveryStatus.PENDING, 1)
        assert row.last_error is not None
        assert "bug in a channel" in row.last_error

    def test_delays_double_up_to_the_maximum(self) -> None:
        policy = RetryPolicy(initial_delay=timedelta(seconds=10), max_delay=timedelta(minutes=1))

        delays = [policy.delay(attempts).total_seconds() for attempts in (1, 2, 3, 4, 100)]

        assert delays == [10, 20, 40, 60, 60]

    def test_needs_at_least_one_attempt(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            RetryPolicy(max_attempts=0)


class TestMetrics:
    async def test_outcomes_cooldowns_and_pending_alerts_are_counted(self, hub: Harness) -> None:
        metrics = Metrics(process_metrics=False)
        flaky = FakeNotifier(failures=[NotificationError("busy", retryable=True)])
        alerts = hub.service(flaky, clock=Clock(), metrics=metrics)
        hub.ended("porch", "old")
        await hub.outbox.enqueue("old", "porch", ["fake"], at=T0 - timedelta(days=2))

        await alerts.handle(hub.ended("porch", "e1"))
        await alerts.handle(hub.ended("porch", "e2"))  # within the cooldown
        await alerts.refresh_metrics()
        pending = metrics.registry.get_sample_value("vision_hub_alerts_pending")
        await alerts.stop()  # e1: first attempt fails and is rescheduled; old: expired

        def count(name: str, **labels: str) -> float | None:
            return metrics.registry.get_sample_value(name, labels)

        assert pending == 2
        assert count("vision_hub_alerts_total", channel="fake", outcome="retry") == 1
        assert count("vision_hub_alerts_total", channel="fake", outcome="expired") == 1
        assert count("vision_hub_alerts_suppressed_total", device="porch") == 1
        assert count("vision_hub_alert_delivery_seconds_count", channel="fake") == 1

    async def test_direct_sending_is_counted_too(self, hub: Harness) -> None:
        metrics = Metrics(process_metrics=False)
        hub.outbox.broken = True
        notifier = FakeNotifier()
        alerts = hub.service(notifier, metrics=metrics)

        await alerts.handle(hub.ended())
        await alerts.stop()

        sent = metrics.registry.get_sample_value(
            "vision_hub_alerts_total", {"channel": "fake", "outcome": "sent"}
        )
        assert sent == 1


class TestConcurrencyAndShutdown:
    async def test_slow_channel_does_not_delay_others(self, hub: Harness) -> None:
        slow, fast = FakeNotifier("slow", delay=1), FakeNotifier("fast")
        alerts = hub.service(slow, fast)
        await alerts.start()

        hub.publish(hub.ended())
        await asyncio.wait_for(fast.delivered.wait(), 0.5)

        assert slow.sent == []
        await alerts.stop()
        assert len(slow.sent) == 1  # drained on shutdown

    async def test_stop_handles_already_queued_events(self, hub: Harness) -> None:
        notifier = FakeNotifier()
        alerts = hub.service(notifier)
        await alerts.start()

        hub.publish(hub.ended("porch", "e1"))
        hub.publish(hub.ended("gate", "e2"))
        await alerts.stop()  # without yielding first: both are still queued

        assert {a.device_id for a in notifier.sent} == {"porch", "gate"}

    async def test_unfinished_deliveries_stay_pending_for_the_next_run(
        self, hub: Harness, log_records: LogRecords
    ) -> None:
        notifier = FakeNotifier(delay=30)
        alerts = hub.service(notifier)
        await alerts.handle(hub.ended())

        await alerts.stop(drain_timeout=0.05)

        assert notifier.sent == []
        assert hub.outbox.statuses() == [(DeliveryStatus.PENDING, 0)]
        assert any(r["event"] == "notifications_interrupted_by_shutdown" for r in log_records())
