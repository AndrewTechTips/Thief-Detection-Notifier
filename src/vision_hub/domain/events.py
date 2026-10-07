"""Events camera workers publish to the rest of the hub. Plain data (JPEG and video bytes, never
numpy arrays), so they can cross from worker threads into asyncio and later onto a message bus."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import assert_never

from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.motion import BoundingBox, MotionEvent


@dataclass(frozen=True, slots=True)
class DeviceStatusChanged:
    device_id: str
    status: DeviceStatus
    at: datetime


@dataclass(frozen=True, slots=True)
class MotionStartedEvent:
    event: MotionEvent


@dataclass(frozen=True, slots=True)
class VideoClip:
    """A short video of an event: the seconds before it was detected, then the event."""

    data: bytes = field(repr=False)
    content_type: str  # "video/webm"
    duration_seconds: float
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class MotionEndedEvent:
    event: MotionEvent
    snapshot_jpeg: bytes = field(repr=False)  # clean evidence frame, full resolution
    annotated_jpeg: bytes = field(repr=False)  # same frame with motion boxes drawn
    thumbnail_jpeg: bytes = field(default=b"", repr=False)  # small annotated preview for lists
    boxes: tuple[BoundingBox, ...] = ()
    snapshot_at: datetime | None = None
    clip: VideoClip | None = None


type CameraEvent = DeviceStatusChanged | MotionStartedEvent | MotionEndedEvent


# Topics are "<kind>.<device_id>"; subscribe with globs such as "motion.ended.*".
MOTION_STARTED = "motion.started"
MOTION_ENDED = "motion.ended"
DEVICE_STATUS = "device.status"


def topic_for(event: CameraEvent) -> str:
    match event:
        case DeviceStatusChanged():
            return f"{DEVICE_STATUS}.{event.device_id}"
        case MotionStartedEvent():
            return f"{MOTION_STARTED}.{event.event.device_id}"
        case MotionEndedEvent():
            return f"{MOTION_ENDED}.{event.event.device_id}"
        case _:  # pragma: no cover - mypy proves the match is exhaustive
            assert_never(event)
