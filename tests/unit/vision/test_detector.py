from collections.abc import Sequence

import numpy as np
import pytest

from vision_hub.core.config import VisionConfig
from vision_hub.domain.motion import DetectionResult
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.detector import MotionDetector
from vision_hub.vision.frame import Frame

type Rect = tuple[int, int, int, int]  # x, y, width, height


def scene(
    rng: np.random.Generator,
    *,
    size: tuple[int, int] = (640, 480),
    brightness: int = 100,
    noise: float = 4.0,
    objects: Sequence[Rect] = (),
    object_value: int = 220,
) -> Frame:
    """A flat background with sensor noise, plus bright rectangles as moving objects."""
    width, height = size
    frame = np.full((height, width, 3), brightness, dtype=np.float32)
    frame += rng.normal(0, noise, frame.shape)
    for x, y, w, h in objects:
        frame[y : y + h, x : x + w] = object_value
    return np.clip(frame, 0, 255).astype(np.uint8)


def warmed_up(
    config: DetectionConfig, rng: np.random.Generator, **scene_args: object
) -> MotionDetector:
    detector = MotionDetector(config)
    for _ in range(config.warmup_frames + 2):
        assert detector.process(scene(rng, **scene_args)).motion is False  # type: ignore[arg-type]
    return detector


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(seed=7)


@pytest.fixture
def config() -> DetectionConfig:
    return DetectionConfig(warmup_frames=3)


class TestWarmup:
    def test_first_frame_initialises_the_background(
        self, config: DetectionConfig, rng: np.random.Generator
    ) -> None:
        result = MotionDetector(config).process(scene(rng))

        assert result == DetectionResult(motion=False, warming_up=True)

    def test_motion_is_ignored_while_warming_up(
        self, config: DetectionConfig, rng: np.random.Generator
    ) -> None:
        detector = MotionDetector(config)
        detector.process(scene(rng))

        results = [detector.process(scene(rng, objects=[(100, 100, 200, 150)])) for _ in range(3)]

        assert all(r.warming_up and not r.motion for r in results)

    def test_reset_starts_over(self, config: DetectionConfig, rng: np.random.Generator) -> None:
        detector = warmed_up(config, rng)

        detector.reset()

        assert detector.process(scene(rng)).warming_up is True


class TestMotion:
    def test_static_noisy_scene_has_no_motion(
        self, config: DetectionConfig, rng: np.random.Generator
    ) -> None:
        detector = warmed_up(config, rng)

        assert not any(detector.process(scene(rng)).motion for _ in range(50))

    def test_moving_object_is_boxed_where_it_is(
        self, config: DetectionConfig, rng: np.random.Generator
    ) -> None:
        detector = warmed_up(config, rng)

        result = detector.process(scene(rng, objects=[(200, 120, 160, 200)]))

        assert result.motion is True
        [box] = result.boxes
        # Blur and dilation grow the region slightly.
        assert abs(box.x - 200) <= 20
        assert abs(box.y - 120) <= 20
        assert abs(box.width - 160) <= 40
        assert abs(box.height - 200) <= 40
        assert result.largest_area_ratio == pytest.approx(160 * 200 / (640 * 480), rel=0.35)

    def test_objects_below_min_area_are_ignored(self, rng: np.random.Generator) -> None:
        small_object = [(300, 200, 25, 25)]  # ~0.2 % of the frame

        default = warmed_up(DetectionConfig(warmup_frames=3), rng)
        sensitive = warmed_up(DetectionConfig(warmup_frames=3, min_motion_area=0.001), rng)

        assert default.process(scene(rng, objects=small_object)).motion is False
        assert sensitive.process(scene(rng, objects=small_object)).motion is True

    def test_multiple_objects_are_sorted_largest_first(
        self, config: DetectionConfig, rng: np.random.Generator
    ) -> None:
        detector = warmed_up(config, rng)

        result = detector.process(scene(rng, objects=[(20, 20, 90, 90), (350, 200, 200, 200)]))

        assert len(result.boxes) == 2
        assert result.boxes[0].area > result.boxes[1].area
        assert result.boxes[0].x > 300

    def test_boxes_are_in_original_frame_coordinates(
        self, config: DetectionConfig, rng: np.random.Generator
    ) -> None:
        full_hd = (1920, 1080)
        detector = warmed_up(config, rng, size=full_hd)

        result = detector.process(scene(rng, size=full_hd, objects=[(1200, 500, 400, 400)]))

        [box] = result.boxes
        assert abs(box.x - 1200) <= 60
        assert abs(box.y - 500) <= 60
        assert box.x + box.width <= 1920
        assert box.y + box.height <= 1080

    def test_grayscale_frames_are_supported(
        self, config: DetectionConfig, rng: np.random.Generator
    ) -> None:
        detector = MotionDetector(config)
        gray = scene(rng)[:, :, 0].copy()
        for _ in range(config.warmup_frames + 2):
            detector.process(gray)

        moving = scene(rng, objects=[(100, 100, 200, 200)])[:, :, 0].copy()

        assert detector.process(moving).motion is True


class TestLighting:
    def test_gradual_drift_is_absorbed_by_the_adaptive_background(
        self, rng: np.random.Generator
    ) -> None:
        """Regression test for the original script, whose frozen background turned dusk or a
        passing cloud into permanent 'motion'."""
        adaptive = warmed_up(DetectionConfig(warmup_frames=3), rng)
        frozen = warmed_up(DetectionConfig(warmup_frames=3, background_learning_rate=1e-6), rng)

        drift = [scene(rng, brightness=100 + step) for step in range(1, 90)]
        adaptive_results = [adaptive.process(frame) for frame in drift]
        frozen_results = [frozen.process(frame) for frame in drift]

        assert not any(r.motion or r.lighting_change for r in adaptive_results)
        assert any(r.motion or r.lighting_change for r in frozen_results)  # the old bug

    def test_sudden_scene_change_resets_instead_of_alarming(
        self, config: DetectionConfig, rng: np.random.Generator
    ) -> None:
        detector = warmed_up(config, rng)

        switched_on = detector.process(scene(rng, brightness=210))
        after = [
            detector.process(scene(rng, brightness=210)) for _ in range(config.warmup_frames + 3)
        ]

        assert switched_on.motion is False
        assert switched_on.lighting_change is True
        assert not any(r.motion for r in after)


class TestRegionOfInterest:
    @pytest.fixture
    def right_half(self) -> DetectionConfig:
        return DetectionConfig(
            warmup_frames=3, roi=(((0.5, 0.0), (1.0, 0.0), (1.0, 1.0), (0.5, 1.0)),)
        )

    def test_motion_outside_the_roi_is_ignored(
        self, right_half: DetectionConfig, rng: np.random.Generator
    ) -> None:
        detector = warmed_up(right_half, rng)

        assert detector.process(scene(rng, objects=[(40, 150, 200, 200)])).motion is False

    def test_motion_inside_the_roi_is_detected(
        self, right_half: DetectionConfig, rng: np.random.Generator
    ) -> None:
        detector = warmed_up(right_half, rng)

        result = detector.process(scene(rng, objects=[(400, 150, 200, 200)]))

        assert result.motion is True
        assert result.boxes[0].x >= 320


class TestRobustness:
    def test_resolution_change_restarts_the_background(
        self, config: DetectionConfig, rng: np.random.Generator
    ) -> None:
        detector = warmed_up(config, rng)

        result = detector.process(scene(rng, size=(320, 240)))

        assert result.warming_up is True
        assert result.motion is False

    @pytest.mark.parametrize(
        "frame",
        [
            np.zeros((10, 10, 3), dtype=np.float32),
            np.zeros((10, 10, 4), dtype=np.uint8),
            np.zeros((0, 10, 3), dtype=np.uint8),
            np.zeros((10,), dtype=np.uint8),
        ],
        ids=["float", "four-channel", "empty", "one-dimensional"],
    )
    def test_invalid_frames_are_rejected(self, config: DetectionConfig, frame: Frame) -> None:
        with pytest.raises(ValueError, match="frame"):
            MotionDetector(config).process(frame)


def test_exposes_its_config(config: DetectionConfig) -> None:
    assert MotionDetector(config).config is config


def test_defaults_come_from_settings() -> None:
    vision = VisionConfig(
        min_motion_area=0.02, threshold=40, blur_kernel_size=11, motion_end_grace_seconds=5
    )

    config = DetectionConfig.from_settings(vision)

    assert (config.min_motion_area, config.pixel_threshold) == (0.02, 40)
    assert (config.blur_kernel_size, config.motion_end_grace_seconds) == (11, 5)
