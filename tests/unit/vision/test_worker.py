import threading
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np
import pytest

from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.events import (
    CameraEvent,
    DeviceStatusChanged,
    MotionEndedEvent,
    MotionStartedEvent,
)
from vision_hub.vision.bridge import FramePacket
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.frame import Frame
from vision_hub.vision.sources import Backoff, SourceError, SyntheticSource, SyntheticSourceConfig
from vision_hub.vision.worker import CameraWorker, EncodingSettings

DETECTION = DetectionConfig(warmup_frames=3, motion_end_grace_seconds=0.5)


class SteppingClock:
    """Each call advances time by a fixed step: makes the worker deterministic."""

    def __init__(self, step: float) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)
        self.step = timedelta(seconds=step)
        self.lock = threading.Lock()

    def __call__(self) -> datetime:
        with self.lock:
            self.now += self.step
            return self.now


class RecordingSink:
    def __init__(self, *, viewers: bool = False, stop_after_ended: int | None = None) -> None:
        self.viewers = viewers
        self.frames: list[FramePacket] = []
        self.events: list[CameraEvent] = []
        self.exits: list[bool] = []
        self.done = threading.Event()
        self._stop_after_ended = stop_after_ended

    @property
    def has_viewers(self) -> bool:
        return self.viewers

    def publish_frame(self, packet: FramePacket) -> None:
        self.frames.append(packet)

    def publish_event(self, event: CameraEvent) -> None:
        self.events.append(event)
        ended = sum(isinstance(e, MotionEndedEvent) for e in self.events)
        if self._stop_after_ended is not None and ended >= self._stop_after_ended:
            self.done.set()

    def worker_exited(self, *, crashed: bool) -> None:
        self.exits.append(crashed)
        self.done.set()

    def statuses(self) -> list[DeviceStatus]:
        return [e.status for e in self.events if isinstance(e, DeviceStatusChanged)]


def synthetic(**overrides: float) -> SyntheticSource:
    fields = {"fps": 10, "visit_every_seconds": 4, "visit_seconds": 1.5, "seed": 3}
    return SyntheticSource(SyntheticSourceConfig.model_validate(fields | overrides), paced=False)


def make_worker(
    sink: RecordingSink, *, source: object = None, step: float = 0.1, fps: float = 10
) -> CameraWorker:
    return CameraWorker(
        device_id="cam-1",
        source=source or synthetic(),  # type: ignore[arg-type]
        detection=DETECTION,
        sink=sink,
        target_fps=fps,
        encoding=EncodingSettings(stream_max_width=320),
        backoff=Backoff(initial=0.001, maximum=0.001),
        clock=SteppingClock(step),
    )


def decode(jpeg: bytes) -> Frame:
    image = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    return np.asarray(image, dtype=np.uint8)


def run_until_done(worker: CameraWorker, sink: RecordingSink, timeout: float = 10) -> None:
    worker.start()
    assert sink.done.wait(timeout), "worker did not reach the expected point in time"
    assert worker.stop(timeout=5)


class TestLifecycle:
    def test_publishes_motion_events_with_jpeg_evidence(self) -> None:
        sink = RecordingSink(viewers=True, stop_after_ended=2)
        worker = make_worker(sink)

        run_until_done(worker, sink)

        started = [e for e in sink.events if isinstance(e, MotionStartedEvent)]
        ended = [e for e in sink.events if isinstance(e, MotionEndedEvent)]
        assert len(started) >= 2
        assert len(ended) >= 2
        clean, annotated = decode(ended[0].snapshot_jpeg), decode(ended[0].annotated_jpeg)
        assert clean.shape == (480, 640, 3)
        assert ended[0].boxes
        assert not np.array_equal(clean, annotated)  # boxes only on the annotated copy
        assert ended[0].event.device_id == "cam-1"

    def test_reports_status_through_its_life(self) -> None:
        sink = RecordingSink(stop_after_ended=1)
        worker = make_worker(sink)

        run_until_done(worker, sink)

        statuses = sink.statuses()
        assert statuses[:2] == [DeviceStatus.STARTING, DeviceStatus.ONLINE]
        assert statuses[-1] is DeviceStatus.STOPPED
        assert sink.exits == [False]
        assert worker.is_alive is False

    def test_live_frames_are_downscaled_jpegs_in_sequence(self) -> None:
        sink = RecordingSink(viewers=True, stop_after_ended=1)

        run_until_done(make_worker(sink), sink)

        sequences = [f.sequence for f in sink.frames]
        assert sequences == list(range(1, len(sequences) + 1))
        assert decode(sink.frames[0].jpeg).shape == (240, 320, 3)
        assert (sink.frames[0].width, sink.frames[0].height) == (320, 240)
        assert any(f.motion for f in sink.frames)

    def test_without_viewers_frames_are_encoded_about_once_a_second(self) -> None:
        sink = RecordingSink(viewers=False, stop_after_ended=2)

        run_until_done(make_worker(sink), sink)

        processed_seconds = (
            sink.frames[-1].captured_at - sink.frames[0].captured_at
        ).total_seconds()
        assert len(sink.frames) <= processed_seconds + 2  # ~1 per simulated second, not 10

    def test_frame_rate_is_limited_to_the_target(self) -> None:
        """Camera delivers 50 fps (0.02 s per frame); only 10 fps are analysed."""
        sink = RecordingSink(viewers=True, stop_after_ended=1)

        run_until_done(make_worker(sink, step=0.02, fps=10), sink)

        gaps = {
            round((b.captured_at - a.captured_at).total_seconds(), 2)
            for a, b in zip(sink.frames, sink.frames[1:], strict=False)
        }
        assert min(gaps) >= 0.1

    def test_stopping_mid_event_still_delivers_it(self) -> None:
        sink = RecordingSink()
        worker = make_worker(sink, source=synthetic(visit_every_seconds=40, visit_seconds=30))
        worker.start()
        deadline = threading.Event()
        for _ in range(200):
            if any(isinstance(e, MotionStartedEvent) for e in sink.events):
                break
            deadline.wait(0.02)

        assert worker.stop(timeout=5)

        assert any(isinstance(e, MotionEndedEvent) for e in sink.events)

    def test_cannot_start_twice(self) -> None:
        sink = RecordingSink(stop_after_ended=1)
        worker = make_worker(sink)
        worker.start()
        try:
            with pytest.raises(RuntimeError, match="already started"):
                worker.start()
        finally:
            worker.stop()

    def test_stop_before_start_is_a_no_op(self) -> None:
        assert make_worker(RecordingSink()).stop() is True


class ExplodingSource:
    name, fps, resolution = "exploding", 10.0, (4, 4)

    def __init__(self, error: Exception) -> None:
        self.error = error

    def open(self) -> None:
        pass

    def read(self) -> Frame:
        raise self.error

    def close(self) -> None:
        pass


class TestFailures:
    def test_stopping_while_reconnecting_exits_promptly(self) -> None:
        sink = RecordingSink()
        worker = CameraWorker(
            device_id="cam-1",
            source=ExplodingSource(SourceError("camera unplugged")),
            detection=DETECTION,
            sink=sink,
            target_fps=10,
            backoff=Backoff(initial=30, maximum=30),  # stuck in a long backoff wait
        )
        worker.start()
        for _ in range(100):
            if DeviceStatus.RECONNECTING in sink.statuses():
                break
            threading.Event().wait(0.01)

        assert worker.stop(timeout=2)
        assert sink.statuses()[-1] is DeviceStatus.STOPPED
        assert sink.exits == [False]

    def test_cleanup_errors_do_not_prevent_shutdown(self) -> None:
        sink = RecordingSink(stop_after_ended=1)
        worker = make_worker(sink)

        def broken_close() -> None:
            raise RuntimeError("tracker bug")

        worker._tracker.close = broken_close  # type: ignore[method-assign]

        run_until_done(worker, sink)

        assert sink.statuses()[-1] is DeviceStatus.STOPPED

    def test_unexpected_errors_crash_the_worker_and_report_failed(self) -> None:
        sink = RecordingSink()

        run_until_done(make_worker(sink, source=ExplodingSource(ZeroDivisionError("bug"))), sink)

        assert sink.statuses()[-1] is DeviceStatus.FAILED
        assert sink.exits == [True]

    def test_a_source_that_gives_up_counts_as_a_crash(self) -> None:
        class GivingUp(ExplodingSource):
            def open(self) -> None:
                raise SourceError("gone")

        sink = RecordingSink()
        worker = make_worker(sink, source=GivingUp(SourceError("gone")))
        worker._source._max_attempts = 1

        run_until_done(worker, sink)

        assert sink.statuses()[-1] is DeviceStatus.FAILED
        assert sink.exits == [True]
