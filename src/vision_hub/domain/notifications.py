"""Outbound alerts: one ``Notifier`` per channel (email now; webhook/Telegram later), and the
outbox that keeps undelivered alerts across restarts."""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from vision_hub.domain.motion import MotionEvent


@dataclass(frozen=True, slots=True)
class Alert:
    device_id: str
    device_name: str
    event: MotionEvent
    image_jpeg: bytes = field(repr=False)  # annotated evidence frame


class NotificationError(Exception):
    """Delivery failed. ``retryable`` is False when trying again cannot help (bad credentials,
    rejected recipient), so the service gives up immediately."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class Notifier(Protocol):
    @property
    def name(self) -> str: ...

    async def send(self, alert: Alert) -> None:
        """Deliver the alert or raise ``NotificationError``."""
        ...


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 8
    initial_delay: timedelta = timedelta(seconds=10)
    max_delay: timedelta = timedelta(minutes=15)
    max_age: timedelta = timedelta(hours=24)  # undelivered alerts older than this are dropped

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            msg = "max_attempts must be at least 1"
            raise ValueError(msg)

    def delay(self, attempts: int) -> timedelta:
        """Wait before the next try, after ``attempts`` failed ones: doubles each time."""
        factor = 1 << min(max(attempts - 1, 0), 30)  # bounded: timedelta overflows eventually
        return min(self.initial_delay * factor, self.max_delay)


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Delivery:
    """One alert to one channel, as stored in the outbox."""

    id: str
    event_id: str
    device_id: str
    channel: str
    attempts: int
    created_at: datetime


class NotificationOutbox(Protocol):
    async def enqueue(
        self, event_id: str, device_id: str, channels: Sequence[str], *, at: datetime
    ) -> None:
        """Store one pending delivery per channel, due now. Enqueuing the same event and
        channel twice has no effect."""
        ...

    async def due(
        self, now: datetime, *, channels: Collection[str], limit: int
    ) -> Sequence[Delivery]:
        """Pending deliveries whose next attempt is due, oldest first."""
        ...

    async def mark_sent(self, delivery_id: str, *, attempts: int, at: datetime) -> None: ...

    async def mark_failed(
        self, delivery_id: str, *, attempts: int, at: datetime, error: str
    ) -> None:
        """Give up on a delivery for good."""
        ...

    async def retry_later(
        self, delivery_id: str, *, attempts: int, next_attempt_at: datetime, error: str
    ) -> None: ...

    async def pending_count(self) -> int: ...

    async def last_alerts(self, *, since: datetime) -> Mapping[str, datetime]:
        """Per device, when its most recent alert was created (restores cooldowns)."""
        ...
