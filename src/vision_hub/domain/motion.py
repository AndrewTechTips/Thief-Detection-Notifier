"""Motion detection results and events. Pure data: no OpenCV or numpy types."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Axis-aligned box in pixel coordinates of the original (full-resolution) frame."""

    x: int
    y: int
    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height

    def scaled(self, factor: float) -> BoundingBox:
        """The same box on an image resized by ``factor`` (e.g. 0.5 for half the width)."""
        return BoundingBox(
            x=round(self.x * factor),
            y=round(self.y * factor),
            width=max(1, round(self.width * factor)),
            height=max(1, round(self.height * factor)),
        )


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Outcome of analysing one frame against the background model."""

    motion: bool
    boxes: tuple[BoundingBox, ...] = ()
    largest_area_ratio: float = 0.0  # biggest moving region, as a fraction of the frame
    changed_ratio: float = 0.0  # fraction of (ROI) pixels that differ from the background
    lighting_change: bool = False  # whole-scene change; background was reset instead
    warming_up: bool = False  # background still settling after start or reset


NO_MOTION = DetectionResult(motion=False)


@dataclass(frozen=True, slots=True)
class MotionEvent:
    """One continuous period of motion on one device."""

    id: str
    device_id: str
    started_at: datetime
    ended_at: datetime | None = None
    peak_area_ratio: float = 0.0
    motion_frames: int = 0
