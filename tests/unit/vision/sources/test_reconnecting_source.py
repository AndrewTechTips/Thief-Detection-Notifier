import random
import threading
import time
from typing import Any

import numpy as np
import pytest

from vision_hub.domain.devices import DeviceStatus
from vision_hub.vision.frame import Frame
from vision_hub.vision.sources import (
    Backoff,
    Pacer,
    ReconnectingSource,
    SourceError,
    SourceStoppedError,
)

FRAME: Frame = np.zeros((4, 4, 3), dtype=np.uint8)


class ScriptedSource:
    """Each ``open``/``read`` call consumes the next scripted outcome: True/a frame = success,
    False/None = SourceError."""

    def __init__(self, opens: list[bool], reads: list[Frame | None] | None = None) -> None:
        self.opens = list(opens)
        self.reads = list(reads or [])
        self.open_calls = 0
        self.close_calls = 0

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def fps(self) -> float | None:
        return 12.0

    @property
    def resolution(self) -> tuple[int, int] | None:
        return (4, 4)

    def open(self) -> None:
        self.open_calls += 1
        if not self.opens.pop(0):
            raise SourceError("open failed")

    def read(self) -> Frame:
        frame = self.reads.pop(0) if self.reads else FRAME
        if frame is None:
            raise SourceError("read failed")
        return frame

    def close(self) -> None:
        self.close_calls += 1


def fast_backoff() -> Backoff:
    return Backoff(initial=0.001, maximum=0.002, jitter=0)


def wrap(
    source: ScriptedSource, *, max_attempts: int | None = None, stop: threading.Event | None = None
) -> tuple[ReconnectingSource, list[DeviceStatus]]:
    statuses: list[DeviceStatus] = []
    wrapped = ReconnectingSource(
        source,
        stop=stop or threading.Event(),
        backoff=fast_backoff(),
        on_status=statuses.append,
        max_attempts=max_attempts,
    )
    return wrapped, statuses


class TestReconnectingSource:
    def test_online_only_once_a_frame_arrives(self) -> None:
        source, statuses = wrap(ScriptedSource([True]))

        source.open()
        assert statuses == [DeviceStatus.STARTING]

        source.read()
        source.read()
        assert statuses == [DeviceStatus.STARTING, DeviceStatus.ONLINE]  # reported once

    def test_failed_opens_are_retried(self) -> None:
        inner = ScriptedSource([False, False, True])
        source, statuses = wrap(inner)

        source.open()
        source.read()

        assert inner.open_calls == 3
        assert statuses == [DeviceStatus.STARTING, DeviceStatus.RECONNECTING, DeviceStatus.ONLINE]

    def test_streams_that_open_but_never_deliver_keep_backing_off(self) -> None:
        """Regression test (found against a live RTSP server): a stream that opens and then
        fails every read must not reset the backoff on each successful open."""
        delays: list[float] = []

        class RecordingBackoff(Backoff):
            def next_delay(self) -> float:
                delay = super().next_delay()
                delays.append(delay)
                return delay / 1000  # keep the test fast

        inner = ScriptedSource([True] * 6, reads=[None] * 5)
        statuses: list[DeviceStatus] = []
        source = ReconnectingSource(
            inner,
            stop=threading.Event(),
            backoff=RecordingBackoff(initial=1, maximum=100, jitter=0),
            on_status=statuses.append,
        )
        source.open()

        source.read()  # five failed reads, then the sixth open finally delivers

        assert delays == [1, 2, 4, 8, 16]
        assert statuses.count(DeviceStatus.ONLINE) == 1

    def test_read_failure_reconnects_and_resumes(self) -> None:
        marker = np.ones((4, 4, 3), dtype=np.uint8)
        inner = ScriptedSource([True, False, True], reads=[None, marker])
        source, statuses = wrap(inner)
        source.open()

        frame = source.read()

        assert frame is marker
        assert inner.close_calls == 1  # the broken capture was released before reopening
        assert statuses[-2:] == [DeviceStatus.RECONNECTING, DeviceStatus.ONLINE]

    def test_gives_up_after_max_attempts(self) -> None:
        source, statuses = wrap(ScriptedSource([False, False, False]), max_attempts=2)

        with pytest.raises(SourceError):
            source.open()

        assert statuses[-1] is DeviceStatus.FAILED

    def test_stop_interrupts_a_long_backoff(self) -> None:
        stop = threading.Event()
        source = ReconnectingSource(
            ScriptedSource([False]), stop=stop, backoff=Backoff(initial=30, maximum=30)
        )
        threading.Timer(0.05, stop.set).start()

        started = time.perf_counter()
        with pytest.raises(SourceStoppedError):
            source.open()

        assert time.perf_counter() - started < 1

    def test_read_after_stop_raises(self) -> None:
        stop = threading.Event()
        source, _ = wrap(ScriptedSource([True]), stop=stop)
        source.open()
        stop.set()

        with pytest.raises(SourceStoppedError):
            source.read()

    def test_delegates_metadata_and_close(self) -> None:
        inner = ScriptedSource([True])
        source, _ = wrap(inner)

        source.close()

        assert (source.name, source.fps, source.resolution) == ("scripted", 12.0, (4, 4))
        assert inner.close_calls == 1


class TestBackoff:
    def test_grows_exponentially_up_to_the_cap(self) -> None:
        backoff = Backoff(initial=1, maximum=10, multiplier=2, jitter=0)

        assert [backoff.next_delay() for _ in range(6)] == [1, 2, 4, 8, 10, 10]

    def test_reset_starts_over(self) -> None:
        backoff = Backoff(initial=1, maximum=10, jitter=0)
        backoff.next_delay()
        backoff.next_delay()

        backoff.reset()

        assert backoff.next_delay() == 1

    def test_jitter_stays_within_bounds(self) -> None:
        backoff = Backoff(initial=10, maximum=10, jitter=0.2, rng=random.Random(4))

        delays = [backoff.next_delay() for _ in range(200)]

        assert all(8 <= d <= 12 for d in delays)
        assert len(set(delays)) > 1

    @pytest.mark.parametrize(
        "kwargs",
        [{"initial": 0}, {"initial": 5, "maximum": 1}, {"multiplier": 0.5}, {"jitter": 1}],
    )
    def test_rejects_invalid_parameters(self, kwargs: dict[str, Any]) -> None:
        with pytest.raises(ValueError, match="invalid backoff"):
            Backoff(**kwargs)


class TestPacer:
    def make(self, fps: float = 10) -> tuple[Pacer, list[float], list[float]]:
        now = [100.0]
        slept: list[float] = []

        def sleep(seconds: float) -> None:
            slept.append(round(seconds, 6))
            now[0] += seconds

        return Pacer(fps, clock=lambda: now[0], sleep=sleep), now, slept

    def test_spaces_calls_one_interval_apart(self) -> None:
        pacer, now, slept = self.make()

        pacer.wait()  # first call never waits
        now[0] += 0.03  # 30 ms of work
        pacer.wait()

        assert slept == [0.07]

    def test_no_sleep_when_work_used_the_whole_interval(self) -> None:
        pacer, now, slept = self.make()
        pacer.wait()

        now[0] += 0.1
        pacer.wait()

        assert slept == []

    def test_does_not_burst_after_falling_behind(self) -> None:
        pacer, now, slept = self.make()
        pacer.wait()

        now[0] += 5  # a long stall
        pacer.wait()
        pacer.wait()

        assert slept == [0.1]  # resumes the normal cadence, no catch-up burst

    def test_reset(self) -> None:
        pacer, _, slept = self.make()
        pacer.wait()

        pacer.reset()
        pacer.wait()

        assert slept == []
