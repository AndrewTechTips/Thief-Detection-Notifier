"""WebSocket protocol (``/api/v1/ws/events``), version 1.

Server -> client messages share an envelope: ``{"type", "v", "ts", "device_id"?, "data"?}``.
Client -> server messages: ``subscribe``, ``unsubscribe`` and ``pong``.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from vision_hub.core.security import utc_now
from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.events import (
    CameraEvent,
    DeviceStatusChanged,
    MotionEndedEvent,
    MotionStartedEvent,
)
from vision_hub.domain.motion import BoundingBox
from vision_hub.schemas.base import ApiSchema, RequestSchema, UtcDateTime

PROTOCOL_VERSION: Literal[1] = 1


class _ServerMessage(ApiSchema):
    v: Literal[1] = PROTOCOL_VERSION
    ts: UtcDateTime = Field(default_factory=utc_now)


class MotionStartedData(ApiSchema):
    event_id: str
    started_at: datetime


class MotionEndedData(MotionStartedData):
    ended_at: datetime | None
    peak_area_ratio: float
    motion_frames: int
    boxes: list[BoundingBox]


class DeviceStatusData(ApiSchema):
    status: DeviceStatus


class SubscriptionData(ApiSchema):
    devices: list[str] | None = Field(description="Subscribed devices; null means all")
    excluded: list[str] = Field(description="Devices excluded while subscribed to all")


class ErrorData(ApiSchema):
    code: str
    message: str


class MotionStartedMessage(_ServerMessage):
    type: Literal["motion.started"] = "motion.started"
    device_id: str
    data: MotionStartedData


class MotionEndedMessage(_ServerMessage):
    type: Literal["motion.ended"] = "motion.ended"
    device_id: str
    data: MotionEndedData


class DeviceStatusMessage(_ServerMessage):
    type: Literal["device.status"] = "device.status"
    device_id: str
    data: DeviceStatusData


class SubscriptionMessage(_ServerMessage):
    type: Literal["subscription"] = "subscription"
    data: SubscriptionData


class PingMessage(_ServerMessage):
    type: Literal["ping"] = "ping"


class ErrorMessage(_ServerMessage):
    type: Literal["error"] = "error"
    data: ErrorData


type ServerMessage = Annotated[
    MotionStartedMessage
    | MotionEndedMessage
    | DeviceStatusMessage
    | SubscriptionMessage
    | PingMessage
    | ErrorMessage,
    Field(discriminator="type"),
]


class SubscribeMessage(RequestSchema):
    """Replace the subscription. ``devices: null`` (or omitted) means every device."""

    type: Literal["subscribe"]
    devices: list[str] | None = Field(default=None, max_length=256)


class UnsubscribeMessage(RequestSchema):
    type: Literal["unsubscribe"]
    devices: list[str] = Field(min_length=1, max_length=256)


class PongMessage(RequestSchema):
    type: Literal["pong"]


type ClientMessage = Annotated[
    SubscribeMessage | UnsubscribeMessage | PongMessage, Field(discriminator="type")
]
client_message_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def to_message(event: CameraEvent) -> ServerMessage:
    match event:
        case DeviceStatusChanged():
            return DeviceStatusMessage(
                device_id=event.device_id, data=DeviceStatusData(status=event.status)
            )
        case MotionStartedEvent(event=motion):
            return MotionStartedMessage(
                device_id=motion.device_id,
                data=MotionStartedData(event_id=motion.id, started_at=motion.started_at),
            )
        case MotionEndedEvent(event=motion):
            return MotionEndedMessage(
                device_id=motion.device_id,
                data=MotionEndedData(
                    event_id=motion.id,
                    started_at=motion.started_at,
                    ended_at=motion.ended_at,
                    peak_area_ratio=motion.peak_area_ratio,
                    motion_frames=motion.motion_frames,
                    boxes=list(event.boxes),
                ),
            )
        case _:  # pragma: no cover - mypy proves the match is exhaustive
            raise AssertionError(event)
