"""Per-device motion detection settings (defaults come from ``VISION_HUB_VISION__*``)."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vision_hub.core.config import VisionConfig

type Point = tuple[
    Annotated[float, Field(ge=0, le=1)],
    Annotated[float, Field(ge=0, le=1)],
]
"""Normalised ``(x, y)``: 0..1 of the frame width/height, so masks survive resolution changes."""

type Polygon = Annotated[tuple[Point, ...], Field(min_length=3, max_length=64)]


class DetectionConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # What counts as motion
    min_motion_area: float = Field(default=0.01, gt=0, lt=1)
    pixel_threshold: int = Field(default=60, ge=1, le=255)
    blur_kernel_size: int = Field(default=21, ge=3, le=99)
    roi: tuple[Polygon, ...] = Field(
        default=(), max_length=16, description="Regions to watch; empty means the whole frame"
    )

    # Background model
    processing_width: int = Field(default=640, ge=160, le=1920)
    background_learning_rate: float = Field(default=0.02, gt=0, le=1)
    warmup_frames: int = Field(default=10, ge=0, le=300)
    lighting_change_ratio: float = Field(default=0.6, gt=0, le=1)

    # Event boundaries (hysteresis)
    min_motion_frames: int = Field(default=2, ge=1, le=60)
    motion_end_grace_seconds: float = Field(default=2.0, ge=0, le=60)
    max_event_seconds: float = Field(default=120.0, gt=0, le=3600)

    # Alerts: on any motion, or only when a person was seen (needs person detection; without
    # it every event alerts)
    alert_on: Literal["motion", "person"] = "motion"

    @field_validator("blur_kernel_size")
    @classmethod
    def _must_be_odd(cls, value: int) -> int:
        if value % 2 == 0:
            msg = "Gaussian blur kernel size must be odd"
            raise ValueError(msg)
        return value

    @classmethod
    def from_settings(cls, vision: VisionConfig) -> Self:
        return cls(
            min_motion_area=vision.min_motion_area,
            pixel_threshold=vision.threshold,
            blur_kernel_size=vision.blur_kernel_size,
            motion_end_grace_seconds=vision.motion_end_grace_seconds,
        )
