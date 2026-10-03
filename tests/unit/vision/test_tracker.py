from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from vision_hub.domain.motion import BoundingBox, DetectionResult
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.frame import Frame
from vision_hub.vision.tracker import (
    MotionEnded,
    MotionStarted,
    MotionTracker,
    TrackerState,
    TrackerUpdate,
)

T0 = datetime(2026, 1, 1, 22, 0, tzinfo=UTC)
QUIET = DetectionResult(motion=False)


def motion(area: float = 0.05) -> DetectionResult:
    return DetectionResult(motion=True, boxes=(BoundingBox(0, 0, 10, 10),), largest_area_ratio=area)


def state_of(tracker: MotionTracker) -> TrackerState:
    """Read through a call so mypy does not keep a stale narrowed type between updates."""
    return tracker.state


def frame(marker: int) -> Frame:
    """Tiny frame whose pixel value identifies which frame it was."""
    return np.full((4, 4, 3), marker, dtype=np.uint8)


class Clock:
    """Feeds frames at a fixed rate, like a 10 fps camera."""

    def __init__(self, tracker: MotionTracker, fps: float = 10) -> None:
        self.tracker = tracker
        self.step = timedelta(seconds=1 / fps)
        self.now = T0
        self.updates: list[TrackerUpdate] = []
        self.count = 0

    def feed(self, detection: DetectionResult, times: int = 1) -> TrackerUpdate | None:
        update = None
        for _ in range(times):
            update = self.tracker.update(frame(self.count % 250), detection, self.now)
            if update is not None:
                self.updates.append(update)
            self.now += self.step
            self.count += 1
        return update

    def started(self) -> list[MotionStarted]:
        return [u for u in self.updates if isinstance(u, MotionStarted)]

    def ended(self) -> list[MotionEnded]:
        return [u for u in self.updates if isinstance(u, MotionEnded)]


@pytest.fixture
def config() -> DetectionConfig:
    return DetectionConfig(min_motion_frames=3, motion_end_grace_seconds=1.0, max_event_seconds=30)


@pytest.fixture
def clock(config: DetectionConfig) -> Clock:
    return Clock(MotionTracker("cam-1", config))


class TestStarting:
    def test_short_blips_are_noise(self, clock: Clock) -> None:
        for _ in range(5):
            clock.feed(motion(), times=2)  # below min_motion_frames=3
            clock.feed(QUIET)

        assert clock.updates == []
        assert state_of(clock.tracker) is TrackerState.IDLE

    def test_event_starts_after_consecutive_motion_frames(self, clock: Clock) -> None:
        clock.feed(motion(), times=2)
        assert state_of(clock.tracker) is TrackerState.ARMING

        update = clock.feed(motion())

        assert isinstance(update, MotionStarted)
        assert update.event.device_id == "cam-1"
        assert update.event.started_at == T0  # back-dated to the first motion frame
        assert update.event.motion_frames == 3
        assert state_of(clock.tracker) is TrackerState.ACTIVE
        assert clock.tracker.current_event == update.event

    def test_single_frame_threshold_starts_immediately(self) -> None:
        tracker = MotionTracker("cam-1", DetectionConfig(min_motion_frames=1))

        assert isinstance(tracker.update(frame(1), motion(), T0), MotionStarted)


class TestEnding:
    def test_gaps_shorter_than_grace_keep_one_event(self, clock: Clock) -> None:
        """Regression test: the original script emailed once per flicker of the contours."""
        for _ in range(6):
            clock.feed(motion(), times=4)
            clock.feed(QUIET, times=5)  # 0.5 s gap < 1 s grace

        clock.feed(QUIET, times=20)

        assert len(clock.started()) == 1
        assert len(clock.ended()) == 1
        assert clock.ended()[0].event.motion_frames == 24

    def test_event_ends_after_grace_at_the_last_motion_frame(self, clock: Clock) -> None:
        clock.feed(motion(), times=5)
        last_motion_at = clock.now - clock.step
        clock.feed(QUIET, times=9)  # 0.9 s of quiet, inside the 1 s grace
        assert state_of(clock.tracker) is TrackerState.ENDING

        update = clock.feed(QUIET)  # 1.0 s of quiet

        assert isinstance(update, MotionEnded)
        assert update.event.started_at == T0
        assert update.event.ended_at == last_motion_at
        assert state_of(clock.tracker) is TrackerState.IDLE
        assert clock.tracker.current_event is None

    def test_long_motion_is_capped(self) -> None:
        clock = Clock(
            MotionTracker("cam-1", DetectionConfig(min_motion_frames=1, max_event_seconds=5))
        )

        clock.feed(motion(), times=200)  # 20 s of continuous motion

        assert len(clock.ended()) == 3
        assert clock.tracker.current_event is not None  # a fourth event is still open
        assert all(
            e.event.ended_at is not None
            and e.event.ended_at - e.event.started_at <= timedelta(seconds=5)
            for e in clock.ended()
        )

    def test_cap_also_applies_while_ending(self) -> None:
        clock = Clock(
            MotionTracker(
                "cam-1",
                DetectionConfig(
                    min_motion_frames=1, max_event_seconds=1, motion_end_grace_seconds=10
                ),
            )
        )

        clock.feed(motion(), times=5)
        clock.feed(QUIET, times=10)

        assert len(clock.ended()) == 1

    def test_new_event_gets_a_new_id_and_fresh_best_frame(self, clock: Clock) -> None:
        clock.feed(motion(area=0.5), times=3)
        clock.feed(QUIET, times=15)
        clock.feed(motion(area=0.1), times=3)
        clock.feed(QUIET, times=15)

        first, second = clock.ended()
        assert first.event.id != second.event.id
        assert second.event.peak_area_ratio == 0.1


class TestBestFrame:
    def test_keeps_the_frame_with_the_largest_moving_area(self, clock: Clock) -> None:
        for area in (0.02, 0.04, 0.30, 0.10, 0.05):
            clock.feed(motion(area))
        clock.feed(QUIET, times=15)

        [ended] = clock.ended()
        assert ended.event.peak_area_ratio == 0.30
        assert ended.best_detection.largest_area_ratio == 0.30
        assert int(ended.best_frame[0, 0, 0]) == 2  # third frame fed (count starts at 0)
        assert ended.best_frame_at == T0 + 2 * clock.step

    def test_best_frame_is_a_copy(self, config: DetectionConfig) -> None:
        tracker = MotionTracker("cam-1", config)
        buffer = frame(9)  # capture backends reuse their buffers
        for i in range(3):
            tracker.update(buffer, motion(area=0.2 if i == 0 else 0.1), T0 + timedelta(seconds=i))

        buffer[:] = 0
        ended = tracker.close()

        assert ended is not None
        assert int(ended.best_frame[0, 0, 0]) == 9


class TestClose:
    def test_closes_an_open_event(self, clock: Clock) -> None:
        clock.feed(motion(), times=4)

        ended = clock.tracker.close()

        assert ended is not None
        assert ended.event.ended_at == T0 + 3 * clock.step
        assert state_of(clock.tracker) is TrackerState.IDLE

    def test_discards_arming(self, clock: Clock) -> None:
        clock.feed(motion())

        assert clock.tracker.close() is None
        assert state_of(clock.tracker) is TrackerState.IDLE

    def test_idle_is_a_no_op(self, clock: Clock) -> None:
        assert clock.tracker.close() is None
