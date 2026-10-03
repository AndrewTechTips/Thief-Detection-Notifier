import asyncio
import threading
from collections.abc import Callable
from typing import Any

import pytest

from vision_hub.core.security import utc_now
from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.events import CameraEvent, DeviceStatusChanged, MotionEndedEvent
from vision_hub.vision.bridge import FramesClosedError
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.fleet import DeviceSpec
from vision_hub.vision.manager import CameraManager, Worker, camera_worker_factory
from vision_hub.vision.sources import Backoff, SyntheticSourceConfig
from vision_hub.vision.worker import WorkerSink

type LogRecords = Callable[[], list[dict[str, Any]]]


def spec(device_id: str = "cam-1", **source: float) -> DeviceSpec:
    fields = {"fps": 20, "visit_every_seconds": 2, "visit_seconds": 0.6, "seed": 1}
    return DeviceSpec(
        id=device_id,
        name=device_id,
        source=SyntheticSourceConfig.model_validate(fields | source),
        detection=DetectionConfig(warmup_frames=3, motion_end_grace_seconds=0.3),
    )


def fast_backoff() -> Backoff:
    return Backoff(initial=0.01, maximum=0.01, jitter=0)


async def wait_for(condition: Callable[[], bool], within: float = 5) -> None:
    async with asyncio.timeout(within):
        while not condition():
            await asyncio.sleep(0.01)


class FakeWorker:
    """Crashes on demand from its own thread, like a real worker would."""

    def __init__(self, sink: WorkerSink, *, crash: bool, stop_ok: bool = True) -> None:
        self.sink, self.crash, self.stop_ok = sink, crash, stop_ok
        self.started = self.stopped = False

    @property
    def is_alive(self) -> bool:
        return self.started and not self.stopped

    def start(self) -> None:
        self.started = True
        if self.crash:
            threading.Thread(target=lambda: self.sink.worker_exited(crashed=True)).start()

    def stop(self, timeout: float = 5.0) -> bool:
        self.stopped = True
        return self.stop_ok


class FakeFactory:
    def __init__(self, *, crash: bool = False, stop_ok: bool = True) -> None:
        self.crash, self.stop_ok = crash, stop_ok
        self.workers: list[FakeWorker] = []

    def __call__(self, _spec: DeviceSpec, sink: WorkerSink) -> Worker:
        worker = FakeWorker(sink, crash=self.crash, stop_ok=self.stop_ok)
        self.workers.append(worker)
        return worker


class TestWithRealWorkers:
    async def test_frames_status_and_events_flow_to_the_loop(self) -> None:
        events: list[CameraEvent] = []
        manager = CameraManager(
            worker_factory=camera_worker_factory(default_fps=20), on_event=events.append
        )

        await manager.start(spec())
        try:
            frame = await asyncio.wait_for(manager.frames("cam-1").next(), timeout=5)
            await wait_for(lambda: any(isinstance(e, MotionEndedEvent) for e in events))
        finally:
            await manager.stop_all()

        assert frame.device_id == "cam-1"
        assert manager.status("cam-1") is DeviceStatus.STOPPED
        assert manager.is_running("cam-1") is False
        assert not [t for t in threading.enumerate() if t.name == "camera-cam-1"]

    async def test_status_goes_online(self) -> None:
        manager = CameraManager(worker_factory=camera_worker_factory(default_fps=20))
        await manager.start(spec())
        try:
            await wait_for(lambda: manager.status("cam-1") is DeviceStatus.ONLINE)
        finally:
            await manager.stop_all()

    async def test_restart_applies_a_new_spec(self) -> None:
        manager = CameraManager(worker_factory=camera_worker_factory(default_fps=20))
        await manager.start(spec())
        try:
            await manager.restart("cam-1", spec(width=320, height=240))
            frame = await asyncio.wait_for(manager.frames("cam-1").next(), timeout=5)
            await wait_for(lambda: (manager.frames("cam-1").latest or frame).width == 320)
        finally:
            await manager.stop_all()

        assert manager.device_ids == ["cam-1"]


class TestSupervision:
    async def test_crashed_workers_are_restarted(self) -> None:
        factory = FakeFactory(crash=True)
        manager = CameraManager(worker_factory=factory, restart_backoff=fast_backoff)

        await manager.start(spec())
        await wait_for(lambda: len(factory.workers) >= 3)
        await manager.stop("cam-1")

        assert len(factory.workers) >= 3

    async def test_clean_exit_is_not_restarted(self) -> None:
        class ExitingFactory(FakeFactory):
            def __call__(self, spec: DeviceSpec, sink: WorkerSink) -> Worker:
                worker = super().__call__(spec, sink)
                sink.worker_exited(crashed=False)
                return worker

        factory = ExitingFactory()
        manager = CameraManager(worker_factory=factory, restart_backoff=fast_backoff)

        await manager.start(spec())
        await wait_for(lambda: not manager.is_running("cam-1"))
        await asyncio.sleep(0.05)

        assert len(factory.workers) == 1
        assert manager.is_wanted("cam-1") is False  # finished: may be started again
        await manager.start(spec())
        assert len(factory.workers) == 2

    async def test_stop_cancels_a_pending_restart(self) -> None:
        factory = FakeFactory(crash=True)
        manager = CameraManager(
            worker_factory=factory, restart_backoff=lambda: Backoff(initial=10, maximum=10)
        )
        await manager.start(spec())
        await wait_for(lambda: "cam-1" in manager._restarts)
        assert manager.is_wanted("cam-1") is True  # not running, but waiting to restart

        await manager.stop("cam-1")
        await asyncio.sleep(0.05)

        assert len(factory.workers) == 1
        assert not manager._restarts

    async def test_online_status_resets_the_restart_backoff(self) -> None:
        backoff = Backoff(initial=1, maximum=8, jitter=0)
        manager = CameraManager(worker_factory=FakeFactory(), restart_backoff=lambda: backoff)
        await manager.start(spec())
        backoff.next_delay()
        backoff.next_delay()

        manager._handle_event(
            DeviceStatusChanged(device_id="cam-1", status=DeviceStatus.ONLINE, at=utc_now())
        )

        assert backoff.next_delay() == 1
        await manager.stop_all()

    async def test_exit_of_a_replaced_worker_is_ignored(self) -> None:
        factory = FakeFactory()
        manager = CameraManager(worker_factory=factory, restart_backoff=fast_backoff)
        await manager.start(spec())
        old = factory.workers[0]
        await manager.restart("cam-1")

        manager._handle_exit("cam-1", old, crashed=True)

        assert manager.is_running("cam-1")
        assert len(factory.workers) == 2
        await manager.stop_all()

    async def test_starting_a_running_camera_is_rejected(self) -> None:
        manager = CameraManager(worker_factory=FakeFactory())
        await manager.start(spec())

        with pytest.raises(ValueError, match="already running"):
            await manager.start(spec())
        await manager.stop_all()

    async def test_slow_stop_is_logged(self, log_records: LogRecords) -> None:
        manager = CameraManager(worker_factory=FakeFactory(stop_ok=False))
        await manager.start(spec())

        await manager.stop("cam-1")

        assert any(r["event"] == "camera_worker_stop_timeout" for r in log_records())

    async def test_listener_errors_do_not_break_the_manager(self, log_records: LogRecords) -> None:
        def broken(_event: CameraEvent) -> None:
            raise RuntimeError("listener bug")

        manager = CameraManager(worker_factory=FakeFactory(), on_event=broken)

        manager._handle_event(
            DeviceStatusChanged(device_id="cam-1", status=DeviceStatus.ONLINE, at=utc_now())
        )

        assert manager.status("cam-1") is DeviceStatus.ONLINE
        assert any(r["event"] == "camera_event_listener_failed" for r in log_records())

    async def test_close_streams_ends_live_views_but_not_cameras(self) -> None:
        manager = CameraManager(worker_factory=FakeFactory())
        await manager.start(spec())

        manager.close_streams()

        with pytest.raises(FramesClosedError):
            await manager.frames("cam-1").next()
        assert manager.is_running("cam-1") is True
        await manager.stop_all()

    async def test_unknown_devices(self) -> None:
        manager = CameraManager(worker_factory=FakeFactory())

        assert manager.status("ghost") is DeviceStatus.STOPPED
        await manager.stop("ghost")
        with pytest.raises(KeyError):
            manager.frames("ghost")
