from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from pydantic import ValidationError

from vision_hub.domain.motion import BoundingBox
from vision_hub.vision.annotate import draw_boxes
from vision_hub.vision.buffer import PreRollBuffer
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.frame import resize_to_width

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def blank(width: int = 1280, height: int = 720) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


class TestResize:
    def test_downscales_keeping_aspect_ratio(self) -> None:
        small, scale = resize_to_width(blank(1920, 1080), 640)

        assert small.shape == (360, 640, 3)
        assert scale == 3.0

    def test_never_upscales(self) -> None:
        frame = blank(320, 240)

        small, scale = resize_to_width(frame, 640)

        assert small is frame
        assert scale == 1.0


class TestPreRollBuffer:
    def test_is_bounded_by_seconds_times_fps(self) -> None:
        buffer = PreRollBuffer(seconds=2, fps=5)
        for i in range(25):
            buffer.add(blank(), T0 + timedelta(seconds=i / 5))

        assert buffer.capacity == 10
        assert len(buffer) == 10
        assert next(at for at, _ in buffer) == T0 + timedelta(seconds=3)  # oldest kept

    def test_stores_downscaled_copies(self) -> None:
        buffer = PreRollBuffer(seconds=1, fps=2, width=320)
        small_source = blank(200, 100)
        buffer.add(blank(), T0)
        buffer.add(small_source, T0)

        (_, big), (_, small) = list(buffer)
        assert big.shape == (180, 320, 3)
        small_source[:] = 255
        assert int(small.max()) == 0  # not aliased to the caller's buffer

    def test_clear(self) -> None:
        buffer = PreRollBuffer(seconds=1, fps=5)
        buffer.add(blank(), T0)

        buffer.clear()

        assert len(buffer) == 0

    @pytest.mark.parametrize(("seconds", "fps", "width"), [(0, 5, 320), (1, 0, 320), (1, 5, 0)])
    def test_rejects_non_positive_sizes(self, seconds: float, fps: float, width: int) -> None:
        with pytest.raises(ValueError, match="positive"):
            PreRollBuffer(seconds=seconds, fps=fps, width=width)


class TestDrawBoxes:
    def test_returns_an_annotated_copy(self) -> None:
        frame = blank(100, 100)

        annotated = draw_boxes(frame, [BoundingBox(10, 10, 30, 30)])

        assert int(frame.max()) == 0  # evidence frame untouched
        assert tuple(annotated[10, 20]) == (0, 255, 0)

    def test_grayscale_input_becomes_color(self) -> None:
        annotated = draw_boxes(np.zeros((50, 50), dtype=np.uint8), [BoundingBox(5, 5, 10, 10)])

        assert annotated.shape == (50, 50, 3)


class TestDetectionConfig:
    def test_blur_kernel_must_be_odd(self) -> None:
        with pytest.raises(ValidationError, match="odd"):
            DetectionConfig(blur_kernel_size=20)

    @pytest.mark.parametrize(
        "roi",
        [
            (((0.0, 0.0), (1.0, 0.0)),),  # fewer than 3 points
            (((0.0, 0.0), (1.5, 0.0), (1.0, 1.0)),),  # outside 0..1
        ],
    )
    def test_roi_polygons_are_validated(self, roi: object) -> None:
        with pytest.raises(ValidationError):
            DetectionConfig(roi=roi)  # type: ignore[arg-type]

    def test_is_immutable_and_strict(self) -> None:
        with pytest.raises(ValidationError):
            DetectionConfig(unknown=1)  # type: ignore[call-arg]
