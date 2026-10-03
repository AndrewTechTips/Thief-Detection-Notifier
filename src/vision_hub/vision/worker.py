"""One camera, one thread: source -> detector -> tracker, plus JPEG encoding.

Everything CPU- or IO-heavy (capture, decoding, detection, encoding) happens here, never on the
event loop (AD-7). OpenCV releases the GIL inside its C++ calls, so cameras run in parallel.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import cv2
import structlog

from vision_hub.core.logging import get_logger
from vision_hub.core.security import utc_now
from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.events import (
    CameraEvent,
    DeviceStatusChanged,
    MotionEndedEvent,
    MotionStartedEvent,
)
from vision_hub.vision.annotate import draw_boxes
from vision_hub.vision.bridge import FramePacket
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.detector import MotionDetector
from vision_hub.vision.frame import Frame, resize_to_width
from vision_hub.vision.sources import (
    Backoff,
    FrameSource,
    ReconnectingSource,
    SourceError,
    SourceStoppedError,
)
from vision_hub.vision.tracker import MotionEnded, MotionStarted, MotionTracker

logger = get_logger(__name__)

IDLE_SNAPSHOT_INTERVAL = timedelta(seconds=1)


class WorkerSink(Protocol):
    """Where a worker publishes; implemented by ``LoopBridge``. Called from the worker thread."""

    @property
    def has_viewers(self) -> bool: ...

    def publish_frame(self, packet: FramePacket) -> None: ...

    def publish_event(self, event: CameraEvent) -> None: ...

    def worker_exited(self, *, crashed: bool) -> None: ...


@dataclass(frozen=True, slots=True)
class EncodingSettings:
    stream_jpeg_quality: int = 70
    stream_max_width: int = 960
    snapshot_jpeg_quality: int = 85
    thumbnail_width: int = 320


class CameraWorker:
    def __init__(
        self,
        *,
        device_id: str,
        source: FrameSource,
        detection: DetectionConfig,
        sink: WorkerSink,
        target_fps: float,
        encoding: EncodingSettings | None = None,
        backoff: Backoff | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.device_id = device_id
        self._stop = threading.Event()
        self._source = ReconnectingSource(
            source, stop=self._stop, backoff=backoff, on_status=self._publish_status
        )
        self._detector = MotionDetector(detection)
        self._tracker = MotionTracker(device_id, detection)
        self._sink = sink
        self._interval = timedelta(seconds=1 / target_fps)
        self._encoding = encoding or EncodingSettings()
        self._clock = clock
        self._thread: threading.Thread | None = None
        self._sequence = 0
        self._last_processed: datetime | None = None
        self._last_streamed: datetime | None = None

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread is not None:
            msg = f"worker for {self.device_id} was already started"
            raise RuntimeError(msg)
        self._thread = threading.Thread(
            target=self._run, name=f"camera-{self.device_id}", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        """Blocking: ask the thread to finish and wait for it. Returns False on timeout.

        Call it via ``asyncio.to_thread`` from async code.
        """
        self._stop.set()
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _run(self) -> None:
        structlog.contextvars.bind_contextvars(device_id=self.device_id)
        logger.info("camera_worker_started", source=self._source.name)
        crashed = False
        try:
            self._source.open()
            while not self._stop.is_set():
                frame = self._source.read()
                at = self._clock()
                if self._due(at):
                    self._process(frame, at)
        except SourceStoppedError:
            pass
        except SourceError:
            logger.exception("camera_source_gave_up")
            crashed = True
        except Exception:
            logger.exception("camera_worker_crashed")
            crashed = True
        finally:
            self._finish(crashed=crashed)

    def _due(self, at: datetime) -> bool:
        """Frame-rate limiting: live cameras keep delivering at their own rate (and must be
        drained to avoid latency); only every ``1/target_fps`` seconds is a frame analysed."""
        if self._last_processed is not None and at - self._last_processed < self._interval:
            return False
        self._last_processed = at
        return True

    def _process(self, frame: Frame, at: datetime) -> None:
        detection = self._detector.process(frame)
        update = self._tracker.update(frame, detection, at)
        if isinstance(update, MotionStarted):
            logger.info("motion_started", event_id=update.event.id)
            self._sink.publish_event(MotionStartedEvent(event=update.event))
        elif isinstance(update, MotionEnded):
            self._publish_motion_ended(update)

        # Encode every frame for live viewers; otherwise just keep the snapshot fresh.
        idle_due = self._last_streamed is None or at - self._last_streamed >= IDLE_SNAPSHOT_INTERVAL
        if self._sink.has_viewers or idle_due:
            self._last_streamed = at
            self._publish_frame(draw_boxes(frame, detection.boxes), at, motion=detection.motion)

    def _publish_frame(self, frame: Frame, at: datetime, *, motion: bool) -> None:
        small, _ = resize_to_width(frame, self._encoding.stream_max_width)
        jpeg = _encode(small, self._encoding.stream_jpeg_quality)
        self._sequence += 1
        self._sink.publish_frame(
            FramePacket(
                device_id=self.device_id,
                sequence=self._sequence,
                captured_at=at,
                width=small.shape[1],
                height=small.shape[0],
                motion=motion,
                jpeg=jpeg,
            )
        )

    def _publish_motion_ended(self, ended: MotionEnded) -> None:
        quality = self._encoding.snapshot_jpeg_quality
        boxes = ended.best_detection.boxes
        logger.info(
            "motion_ended",
            event_id=ended.event.id,
            motion_frames=ended.event.motion_frames,
            peak_area_ratio=round(ended.event.peak_area_ratio, 4),
        )
        annotated = draw_boxes(ended.best_frame, boxes)
        thumbnail, _ = resize_to_width(annotated, self._encoding.thumbnail_width)
        self._sink.publish_event(
            MotionEndedEvent(
                event=ended.event,
                snapshot_jpeg=_encode(ended.best_frame, quality),
                annotated_jpeg=_encode(annotated, quality),
                thumbnail_jpeg=_encode(thumbnail, quality),
                boxes=boxes,
                snapshot_at=ended.best_frame_at,
            )
        )

    def _publish_status(self, status: DeviceStatus) -> None:
        logger.info("camera_status", status=status)
        self._sink.publish_event(
            DeviceStatusChanged(device_id=self.device_id, status=status, at=self._clock())
        )

    def _finish(self, *, crashed: bool) -> None:
        try:
            if (ended := self._tracker.close()) is not None:
                self._publish_motion_ended(ended)  # camera stopped mid-event
        except Exception:
            logger.exception("camera_worker_cleanup_failed")
        self._source.close()
        self._publish_status(DeviceStatus.FAILED if crashed else DeviceStatus.STOPPED)
        logger.info("camera_worker_stopped", crashed=crashed)
        self._sink.worker_exited(crashed=crashed)


def _encode(frame: Frame, quality: int) -> bytes:
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:  # pragma: no cover - only fails for invalid images, which validation prevents
        msg = "JPEG encoding failed"
        raise RuntimeError(msg)
    return buffer.tobytes()
