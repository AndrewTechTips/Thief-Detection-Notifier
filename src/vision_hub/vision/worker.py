"""One camera, one thread: source -> detector -> tracker, plus JPEG encoding.

Everything CPU- or IO-heavy (capture, decoding, detection, encoding) happens here, never on the
event loop (AD-7). OpenCV releases the GIL inside its C++ calls, so cameras run in parallel.
"""

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol

import cv2
import structlog

from vision_hub.core.logging import get_logger
from vision_hub.core.metrics import CameraMetrics, Metrics
from vision_hub.core.security import utc_now
from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.events import (
    CameraEvent,
    DeviceStatusChanged,
    MotionEndedEvent,
    MotionStartedEvent,
    VideoClip,
)
from vision_hub.domain.motion import BoundingBox, MotionEvent
from vision_hub.vision.annotate import draw_boxes
from vision_hub.vision.bridge import FramePacket
from vision_hub.vision.clip import ClipRecorder, ClipSettings
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.detector import MotionDetector
from vision_hub.vision.frame import Frame, resize_to_width
from vision_hub.vision.persons import PersonSettings, should_alert
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
        metrics: CameraMetrics | None = None,
        clips: ClipSettings | None = None,
        persons: PersonSettings | None = None,
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
        self._metrics = metrics or Metrics(process_metrics=False).camera(device_id)
        self._clip = ClipRecorder(clips, fps=target_fps) if clips else None
        self._persons = persons
        self._alert_on = detection.alert_on
        # Person checks of the open event: the best score so far (None until a check worked),
        # when the next check is due (None outside events), and whether the model failed.
        self._person_best: float | None = None
        self._person_due: datetime | None = None
        self._person_failed = False
        self._thread: threading.Thread | None = None
        self._sequence = 0
        self._next_due: datetime | None = None
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
        drained to avoid latency); frames are analysed on a fixed ``1/target_fps`` schedule.

        The schedule tolerates a quarter interval of jitter. Comparing each frame with the
        previous analysed one instead loses up to half the frames when the camera runs at the
        target rate: a frame 99 ms after the last misses a 100 ms interval, so the next
        analysed frame is 200 ms later.
        """
        if self._next_due is not None and at < self._next_due - self._interval / 4:
            return False
        if self._next_due is None or at - self._next_due > self._interval:
            self._next_due = at + self._interval  # first frame, or fell behind: start over
        else:
            self._next_due += self._interval
        return True

    def _process(self, frame: Frame, at: datetime) -> None:
        with self._metrics.processing_seconds.time():
            self._analyse(frame, at)
        self._metrics.frames_analysed.inc()

    def _analyse(self, frame: Frame, at: datetime) -> None:
        detection = self._detector.process(frame)
        update = self._tracker.update(frame, detection, at)
        if isinstance(update, MotionStarted):
            logger.info("motion_started", event_id=update.event.id)
            self._sink.publish_event(MotionStartedEvent(event=update.event))
            self._clip_step(lambda clip: clip.start())
            self._person_best, self._person_due, self._person_failed = None, at, False
        self._clip_step(lambda clip: clip.add(frame, at))
        if self._person_due is not None and at >= self._person_due:
            self._check_person(frame)
            self._person_due = None if self._person_found() else at + self._person_interval()
        if isinstance(update, MotionEnded):
            self._publish_motion_ended(update, self._finish_clip())

        # Encode every frame for live viewers; otherwise just keep the snapshot fresh.
        idle_due = self._last_streamed is None or at - self._last_streamed >= IDLE_SNAPSHOT_INTERVAL
        if self._sink.has_viewers or idle_due:
            self._last_streamed = at
            jpeg, width, height = encode_stream_frame(frame, detection.boxes, self._encoding)
            self._metrics.frames_streamed.inc()
            self._sequence += 1
            self._sink.publish_frame(
                FramePacket(
                    device_id=self.device_id,
                    sequence=self._sequence,
                    captured_at=at,
                    width=width,
                    height=height,
                    motion=detection.motion,
                    jpeg=jpeg,
                )
            )

    def _person_interval(self) -> timedelta:
        seconds = self._persons.check_interval_seconds if self._persons else 1.0
        return timedelta(seconds=seconds)

    def _person_found(self) -> bool:
        persons = self._persons
        return (
            persons is not None
            and self._person_best is not None
            and self._person_best >= persons.threshold
        )

    def _check_person(self, frame: Frame) -> None:
        """One look for a person. A failing model is logged once per event and stops the
        checks; the event then alerts as if there were no person detection."""
        if self._persons is None or self._person_failed:
            return
        try:
            people = self._persons.detector.detect(frame)
        except Exception:
            logger.exception("person_check_failed")
            self._person_failed = True
            return
        score = people[0].confidence if people else 0.0
        self._person_best = max(self._person_best or 0.0, score)

    def _judge(self, ended: MotionEnded) -> MotionEvent:
        """The finished event with its person verdict and whether it alerts. The frame with the
        most motion gets a last look, in case the person passed between two checks."""
        if self._persons is not None and not self._person_found():
            self._check_person(ended.best_frame)
        confidence = None if self._person_failed else self._person_best
        threshold = self._persons.threshold if self._persons else 1.0
        self._person_due = None
        return replace(
            ended.event,
            person=None if confidence is None else confidence >= threshold,
            person_confidence=confidence,
            alert=should_alert(self._alert_on, confidence, threshold),
        )

    def _clip_step(self, step: Callable[[ClipRecorder], None]) -> None:
        """Clips are a bonus: a failure drops the clip, never the detection or the alert."""
        if self._clip is None:
            return
        try:
            step(self._clip)
        except Exception:
            logger.exception("clip_failed")
            self._clip.abort()

    def _finish_clip(self) -> VideoClip | None:
        if self._clip is None:
            return None
        try:
            clip = self._clip.finish()
        except Exception:
            logger.exception("clip_failed")
            self._clip.abort()
            return None
        if clip is not None:
            logger.info(
                "clip_recorded", seconds=round(clip.duration_seconds, 1), bytes=len(clip.data)
            )
        return clip

    def _publish_motion_ended(self, ended: MotionEnded, clip: VideoClip | None = None) -> None:
        quality = self._encoding.snapshot_jpeg_quality
        boxes = ended.best_detection.boxes
        event = self._judge(ended)
        logger.info(
            "motion_ended",
            event_id=event.id,
            motion_frames=event.motion_frames,
            peak_area_ratio=round(event.peak_area_ratio, 4),
            person_confidence=event.person_confidence,
            alert=event.alert,
        )
        self._metrics.motion_events.inc()
        annotated = draw_boxes(ended.best_frame, boxes)
        thumbnail, _ = resize_to_width(annotated, self._encoding.thumbnail_width)
        self._sink.publish_event(
            MotionEndedEvent(
                event=event,
                snapshot_jpeg=_encode(ended.best_frame, quality),
                annotated_jpeg=_encode(annotated, quality),
                thumbnail_jpeg=_encode(thumbnail, quality),
                boxes=boxes,
                snapshot_at=ended.best_frame_at,
                clip=clip,
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
                self._publish_motion_ended(ended, self._finish_clip())  # stopped mid-event
        except Exception:
            logger.exception("camera_worker_cleanup_failed")
        if self._clip is not None:
            self._clip.abort()  # temporary files of a clip that never finished
        self._source.close()
        self._publish_status(DeviceStatus.FAILED if crashed else DeviceStatus.STOPPED)
        logger.info("camera_worker_stopped", crashed=crashed)
        self._sink.worker_exited(crashed=crashed)


def encode_stream_frame(
    frame: Frame, boxes: Sequence[BoundingBox], settings: EncodingSettings
) -> tuple[bytes, int, int]:
    """Live-view JPEG and its size. Downscales *before* drawing, so boxes are drawn on the
    small image, and a frame without motion is encoded without any copy."""
    small, scale = resize_to_width(frame, settings.stream_max_width)
    if boxes:
        small = draw_boxes(small, [box.scaled(1 / scale) for box in boxes])
    return _encode(small, settings.stream_jpeg_quality), small.shape[1], small.shape[0]


def _encode(frame: Frame, quality: int) -> bytes:
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:  # pragma: no cover - only fails for invalid images, which validation prevents
        msg = "JPEG encoding failed"
        raise RuntimeError(msg)
    return buffer.tobytes()
