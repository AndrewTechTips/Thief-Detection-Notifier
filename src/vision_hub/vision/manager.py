"""Owns the camera workers: start/stop/restart, status tracking and crash supervision.

All state lives on the event loop thread; workers report back through ``LoopBridge``. Stopping
a worker joins its thread via ``asyncio.to_thread``, so even shutdown never blocks the loop.
"""

import asyncio
from collections.abc import Callable
from typing import Protocol, cast

from vision_hub.core.logging import get_logger
from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.events import CameraEvent, DeviceStatusChanged, MotionEndedEvent
from vision_hub.domain.motion import MotionEvent
from vision_hub.vision.bridge import LatestFrame, LoopBridge
from vision_hub.vision.fleet import DeviceSpec
from vision_hub.vision.sources import Backoff, create_source
from vision_hub.vision.worker import CameraWorker, EncodingSettings, WorkerSink

logger = get_logger(__name__)


class Worker(Protocol):
    @property
    def is_alive(self) -> bool: ...

    def start(self) -> None: ...

    def stop(self, timeout: float = ...) -> bool: ...


type WorkerFactory = Callable[[DeviceSpec, WorkerSink], Worker]
type EventListener = Callable[[CameraEvent], None]


def camera_worker_factory(
    *, default_fps: float, encoding: EncodingSettings | None = None
) -> WorkerFactory:
    def build(spec: DeviceSpec, sink: WorkerSink) -> Worker:
        return CameraWorker(
            device_id=spec.id,
            source=create_source(spec.source),
            detection=spec.detection,
            sink=sink,
            target_fps=spec.target_fps or default_fps,
            encoding=encoding,
        )

    return build


class CameraManager:
    def __init__(
        self,
        *,
        worker_factory: WorkerFactory,
        on_event: EventListener | None = None,
        restart_backoff: Callable[[], Backoff] = lambda: Backoff(initial=1, maximum=60),
        stop_timeout: float = 5.0,
    ) -> None:
        self._worker_factory = worker_factory
        self._on_event = on_event or (lambda _event: None)
        self._restart_backoff = restart_backoff
        self._stop_timeout = stop_timeout
        self._specs: dict[str, DeviceSpec] = {}
        self._workers: dict[str, Worker] = {}
        self._frames: dict[str, LatestFrame] = {}
        self._statuses: dict[str, DeviceStatus] = {}
        self._last_events: dict[str, MotionEvent] = {}
        self._backoffs: dict[str, Backoff] = {}
        self._restarts: dict[str, asyncio.Task[None]] = {}
        self._wanted: set[str] = set()  # devices that should be running

    @property
    def device_ids(self) -> list[str]:
        return sorted(self._specs)

    def is_running(self, device_id: str) -> bool:
        return device_id in self._workers

    def status(self, device_id: str) -> DeviceStatus:
        return self._statuses.get(device_id, DeviceStatus.STOPPED)

    def last_event(self, device_id: str) -> MotionEvent | None:
        return self._last_events.get(device_id)

    def frames(self, device_id: str) -> LatestFrame:
        """Live frames of a device (raises ``KeyError`` for unknown devices)."""
        return self._frames[device_id]

    async def start(self, spec: DeviceSpec) -> None:
        if spec.id in self._workers or spec.id in self._restarts:
            msg = f"camera {spec.id} is already running"
            raise ValueError(msg)
        self._specs[spec.id] = spec
        self._wanted.add(spec.id)
        self._backoffs[spec.id] = self._restart_backoff()
        self._launch(spec)

    async def stop(self, device_id: str) -> None:
        self._wanted.discard(device_id)
        if (pending := self._restarts.pop(device_id, None)) is not None:
            pending.cancel()
        worker = self._workers.pop(device_id, None)
        if worker is None:
            return
        if not await asyncio.to_thread(worker.stop, self._stop_timeout):
            # Usually a capture blocked inside a long read; the daemon thread exits later.
            logger.warning("camera_worker_stop_timeout", device_id=device_id)

    async def restart(self, device_id: str, spec: DeviceSpec | None = None) -> None:
        """Restart a device, optionally with new settings (e.g. updated detection config)."""
        spec = spec or self._specs[device_id]
        await self.stop(device_id)
        await self.start(spec)

    async def forget(self, device_id: str) -> None:
        """Stop a device and drop all its state (it was deleted)."""
        await self.stop(device_id)
        for state in (self._specs, self._frames, self._statuses, self._last_events, self._backoffs):
            state.pop(device_id, None)

    async def stop_all(self) -> None:
        await asyncio.gather(*(self.stop(device_id) for device_id in list(self._specs)))

    def _launch(self, spec: DeviceSpec) -> None:
        frames = self._frames.setdefault(spec.id, LatestFrame())
        worker: Worker | None = None

        def on_exit(crashed: bool) -> None:
            # Always set: the worker is assigned before its thread starts.
            self._handle_exit(spec.id, cast("Worker", worker), crashed=crashed)

        bridge = LoopBridge(
            asyncio.get_running_loop(), frames=frames, on_event=self._handle_event, on_exit=on_exit
        )
        worker = self._worker_factory(spec, bridge)
        self._workers[spec.id] = worker
        worker.start()

    def _handle_event(self, event: CameraEvent) -> None:
        if isinstance(event, DeviceStatusChanged):
            self._statuses[event.device_id] = event.status
            if event.status is DeviceStatus.ONLINE and event.device_id in self._backoffs:
                self._backoffs[event.device_id].reset()
        elif isinstance(event, MotionEndedEvent):
            self._last_events[event.event.device_id] = event.event
        try:
            self._on_event(event)
        except Exception:
            logger.exception("camera_event_listener_failed", event_type=type(event).__name__)

    def _handle_exit(self, device_id: str, worker: Worker, *, crashed: bool) -> None:
        if self._workers.get(device_id) is not worker:
            return  # a replaced or explicitly stopped worker
        del self._workers[device_id]
        if not crashed or device_id not in self._wanted:
            return
        delay = self._backoffs[device_id].next_delay()
        logger.warning(
            "camera_worker_restart_scheduled", device_id=device_id, delay_s=round(delay, 1)
        )
        self._restarts[device_id] = asyncio.get_running_loop().create_task(
            self._restart_later(device_id, delay), name=f"restart-{device_id}"
        )

    async def _restart_later(self, device_id: str, delay: float) -> None:
        # stop() cancels this task and start() refuses while it is pending, so after the sleep
        # the device is still wanted and has no worker.
        await asyncio.sleep(delay)
        self._restarts.pop(device_id, None)
        logger.info("camera_worker_restarting", device_id=device_id)
        self._launch(self._specs[device_id])
