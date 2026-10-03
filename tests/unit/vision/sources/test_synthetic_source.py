import time
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.detector import MotionDetector
from vision_hub.vision.frame import Frame
from vision_hub.vision.sources import SourceError, SyntheticSource, SyntheticSourceConfig
from vision_hub.vision.tracker import MotionEnded, MotionStarted, MotionTracker

# 2 s period at 10 fps: 15 empty frames, then a 5-frame (0.5 s) visit.
CONFIG = SyntheticSourceConfig(fps=10, visit_every_seconds=2, visit_seconds=0.5, seed=1)


def bright_pixels(frame: Frame) -> int:
    """The figure is drawn at 200-210; the empty room never exceeds ~190 even with noise."""
    return int(np.count_nonzero(frame > 195))


@pytest.fixture
def source() -> SyntheticSource:
    source = SyntheticSource(CONFIG, paced=False)
    source.open()
    return source


def test_frames_have_the_configured_shape(source: SyntheticSource) -> None:
    frame = source.read()

    assert frame.shape == (480, 640, 3)
    assert frame.dtype == np.uint8
    assert source.resolution == (640, 480)
    assert source.fps == 10
    assert source.name == "synthetic 640x480"


def test_visits_follow_the_schedule(source: SyntheticSource) -> None:
    frames = [source.read() for _ in range(40)]

    visiting = [bright_pixels(f) > 1000 for f in frames]

    assert visiting == [source.is_visiting(n) for n in range(40)]
    assert visiting[:20] == [False] * 15 + [True] * 5


def test_same_seed_gives_identical_frames() -> None:
    first, second = SyntheticSource(CONFIG, paced=False), SyntheticSource(CONFIG, paced=False)
    first.open()
    second.open()

    assert all(np.array_equal(first.read(), second.read()) for _ in range(5))


def test_paced_source_runs_at_its_frame_rate() -> None:
    source = SyntheticSource(SyntheticSourceConfig(fps=50, seed=1))
    source.open()

    started = time.perf_counter()
    for _ in range(6):
        source.read()

    assert time.perf_counter() - started >= 0.09  # 5 intervals of 20 ms


def test_reading_requires_open() -> None:
    source = SyntheticSource(CONFIG, paced=False)

    with pytest.raises(SourceError, match="not open"):
        source.read()


def test_close_releases_the_scene(source: SyntheticSource) -> None:
    source.close()

    assert source.resolution is None


def test_each_visit_becomes_exactly_one_motion_event() -> None:
    """End to end with the real detector and tracker: 5 periods -> 5 events."""
    config = SyntheticSourceConfig(fps=10, visit_every_seconds=6, visit_seconds=3, seed=2)
    source = SyntheticSource(config, paced=False)
    source.open()
    detection = DetectionConfig(warmup_frames=5, motion_end_grace_seconds=1)
    detector, tracker = MotionDetector(detection), MotionTracker("synthetic", detection)
    now, step = datetime(2026, 1, 1, tzinfo=UTC), timedelta(seconds=0.1)

    updates = []
    for _ in range(5 * 60 + 20):  # 5 periods of 6 s, then 2 s so the last event can end
        frame = source.read()
        if (update := tracker.update(frame, detector.process(frame), now)) is not None:
            updates.append(update)
        now += step

    assert sum(isinstance(u, MotionStarted) for u in updates) == 5
    assert sum(isinstance(u, MotionEnded) for u in updates) == 5
