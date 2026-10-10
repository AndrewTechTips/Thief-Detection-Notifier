from datetime import UTC, datetime, timedelta

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from vision_hub.domain.motion import BoundingBox
from vision_hub.vision.annotate import AMBER, AMBER_DIM, draw_boxes
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

    @pytest.mark.parametrize(
        ("width", "height", "target", "expected"),
        [
            (1920, 1080, 960, (540, 960)),  # one exact halving
            (2560, 1440, 960, (540, 960)),  # halve to 1280, then finish linearly
            (3840, 2160, 640, (360, 640)),  # halve twice, then finish
            (1001, 601, 500, (300, 500)),  # odd sizes
        ],
    )
    def test_any_size_lands_on_the_target_width(
        self, width: int, height: int, target: int, expected: tuple[int, int]
    ) -> None:
        small, scale = resize_to_width(blank(width, height), target)

        assert small.shape[:2] == expected
        assert scale == pytest.approx(width / target)

    def test_matches_an_area_average_closely(self) -> None:
        rng = np.random.default_rng(1)
        noise = rng.integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
        frame = np.asarray(cv2.GaussianBlur(noise, (5, 5), 0), np.uint8)

        small, _ = resize_to_width(frame, 640)

        reference = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_AREA)
        assert np.abs(small.astype(int) - reference.astype(int)).mean() < 3

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
        assert tuple(annotated[10, 12]) == AMBER  # a corner bracket
        assert tuple(annotated[10, 25]) == AMBER_DIM  # the outline between corners
        assert int(annotated[25, 25].max()) == 0  # inside stays clean

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


def test_boxes_scale_with_the_image() -> None:
    box = BoundingBox(x=100, y=50, width=300, height=3)

    assert box.scaled(0.5) == BoundingBox(x=50, y=25, width=150, height=2)
    assert box.scaled(0.1).height == 1  # never collapses to nothing
