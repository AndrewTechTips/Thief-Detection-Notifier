"""Device API models. Source credentials are write-only: ``RtspSourceConfig.password`` is
excluded from every response (``has_password`` reports whether one is set)."""

from datetime import datetime

from pydantic import Field

from vision_hub.domain.devices import DeviceStatus
from vision_hub.schemas.base import ApiSchema, RequestSchema
from vision_hub.services.devices import DeviceView
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.fleet import DeviceId
from vision_hub.vision.sources import SourceConfig


class StreamInfo(ApiSchema):
    width: int
    height: int
    last_frame_at: datetime


class LastEvent(ApiSchema):
    id: str
    started_at: datetime
    ended_at: datetime | None
    peak_area_ratio: float


class DeviceOut(ApiSchema):
    id: str
    name: str
    enabled: bool = Field(description="Started automatically when the hub starts")
    target_fps: float | None = Field(description="Analysed frames per second; null = default")
    retention_days: int | None = Field(description="Days events are kept; null = hub default")
    source: SourceConfig
    detection: DetectionConfig
    status: DeviceStatus
    running: bool
    stream: StreamInfo | None = Field(description="Latest live frame, if the camera produced one")
    last_event: LastEvent | None

    @classmethod
    def from_view(cls, view: DeviceView) -> DeviceOut:
        frame, event = view.latest_frame, view.last_event
        return cls(
            id=view.spec.id,
            name=view.spec.name,
            enabled=view.spec.enabled,
            target_fps=view.spec.target_fps,
            retention_days=view.spec.retention_days,
            source=view.spec.source,
            detection=view.spec.detection,
            status=view.status,
            running=view.running,
            stream=(
                StreamInfo(width=frame.width, height=frame.height, last_frame_at=frame.captured_at)
                if frame
                else None
            ),
            last_event=LastEvent.model_validate(event) if event else None,
        )


class DeviceCreate(RequestSchema):
    id: DeviceId
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    target_fps: float | None = Field(default=None, gt=0, le=60)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    source: SourceConfig
    detection: DetectionConfig | None = Field(
        default=None, description="Omit to use the hub-wide defaults"
    )


class DeviceUpdate(RequestSchema):
    """Partial update: only the fields present in the request change."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    target_fps: float | None = Field(default=None, gt=0, le=60)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    source: SourceConfig | None = None
    detection: DetectionConfig | None = None


class SourceTestRequest(RequestSchema):
    source: SourceConfig


class SourceTestResult(ApiSchema):
    ok: bool
    elapsed_ms: float
    width: int | None
    height: int | None
    fps: float | None
    error: str | None
