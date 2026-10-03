"""Outbound alerts: one ``Notifier`` per channel (email now; webhook/Telegram later)."""

from dataclasses import dataclass, field
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
