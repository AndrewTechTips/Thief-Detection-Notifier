"""Records what the hub sees on each demo camera, for the public demo: a fake hub in the
browser replays it to the real dashboard (AD-24).

Each camera's loop runs through a real ``CameraWorker`` three times in a row, the first pass
warming the background model up. From the middle pass the export keeps:

* per analysed frame, its time in the loop and what the live stream's ``X-Detections`` header
  would carry (motion boxes as fractions of the picture, the person score once found);
* per event, its timing within the loop, the verdict, and the snapshot, annotated copy,
  thumbnail and clip the hub would store.

Time comes from the frames read, not the wall clock, and person checks run inline instead of on
a background thread, so the export is the same on every machine and every run.
"""

import json
import shutil
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import cv2

from vision_hub.domain.events import CameraEvent, MotionEndedEvent
from vision_hub.realtime.mjpeg import detections
from vision_hub.vision.bridge import FramePacket
from vision_hub.vision.clip import ClipSettings
from vision_hub.vision.fleet import DeviceSpec
from vision_hub.vision.frame import Frame
from vision_hub.vision.persons import PersonChecker, PersonDetector
from vision_hub.vision.sources import SourceStoppedError, VideoFileSource, VideoFileSourceConfig
from vision_hub.vision.worker import CameraWorker

PASSES = 3
FORMAT_VERSION = 1
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


class InlinePersonChecker(PersonChecker):
    """Runs each check at once, in the caller's thread: deterministic, for exports and tests."""

    def submit(self, task: Callable[[], None]) -> Future[None]:
        future: Future[None] = Future()
        task()
        future.set_result(None)
        return future


@dataclass(frozen=True, slots=True)
class CameraExport:
    camera: str
    frames: int
    events: int
    people: int


def export_demo(
    cameras: list[DeviceSpec], detector: PersonDetector, output: Path, *, threshold: float = 0.5
) -> list[CameraExport]:
    """Writes ``manifest.json`` and each camera's files (``<id>/...``) into ``output``."""
    output.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"version": FORMAT_VERSION, "cameras": []}
    summary = []
    for spec in cameras:
        if not isinstance(spec.source, VideoFileSourceConfig):
            msg = f"camera {spec.id} must play a video file to be exported"
            raise ValueError(msg)
        checker = InlinePersonChecker(detector, threshold=threshold)
        try:
            camera = _export_camera(spec, spec.source.path, checker, output / spec.id)
        finally:
            checker.close()
        manifest["cameras"].append(camera)
        summary.append(
            CameraExport(
                camera=spec.id,
                frames=len(camera["frames"]),
                events=len(camera["events"]),
                people=sum(1 for event in camera["events"] if event["person"]),
            )
        )
    (output / "manifest.json").write_text(json.dumps(manifest, separators=(",", ":")))
    return summary


def _export_camera(
    spec: DeviceSpec, loop: Path, checker: PersonChecker, directory: Path
) -> dict[str, Any]:
    fps, length = _loop_shape(loop)
    duration = length / fps
    source = _CountingSource(
        VideoFileSource(VideoFileSourceConfig(path=loop, loop=True), paced=False),
        limit=length * PASSES,
    )
    sink = _RecordingSink()
    worker = CameraWorker(
        device_id=spec.id,
        source=source,
        detection=spec.detection,
        sink=sink,
        target_fps=spec.target_fps or 10.0,
        clock=lambda: _T0 + timedelta(seconds=source.reads / fps),
        clips=ClipSettings(),
        persons=checker,
    )
    worker.start()
    if not sink.done.wait(timeout=600):  # pragma: no cover - a hung export
        msg = f"export of {spec.id} did not finish"
        raise RuntimeError(msg)
    worker.stop()

    def offset(at: datetime) -> float:
        return (at - _T0).total_seconds() - duration

    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(loop, directory / "loop.webm")
    frames = []
    for packet in sink.frames:
        t = offset(packet.captured_at)
        if 0 <= t < duration:
            found = detections(packet)
            frames.append([round(t, 3), json.loads(found) if found else None])
    events: list[dict[str, Any]] = []
    for event in sink.ended:
        start = offset(event.event.started_at)
        if not 0 <= start < duration or event.event.ended_at is None:
            continue
        events.append(_export_event(event, start, offset, directory, spec.id, len(events) + 1))
    height, width = _frame_size(loop)
    return {
        "id": spec.id,
        "name": spec.name,
        "target_fps": spec.target_fps,
        "retention_days": spec.retention_days,
        "source": spec.source.model_dump(mode="json"),
        "detection": spec.detection.model_dump(mode="json"),
        "loop": f"{spec.id}/loop.webm",
        "duration": round(duration, 3),
        "width": width,
        "height": height,
        "frames": frames,
        "events": events,
    }


def _export_event(
    ended: MotionEndedEvent,
    start: float,
    offset: Callable[[datetime], float],
    directory: Path,
    camera: str,
    number: int,
) -> dict[str, Any]:
    event = ended.event
    if event.ended_at is None:  # pragma: no cover - filtered by the caller
        msg = "only finished events can be exported"
        raise ValueError(msg)
    end = offset(event.ended_at)
    files = {}
    for kind, data in (
        ("clean", ended.snapshot_jpeg),
        ("annotated", ended.annotated_jpeg),
        ("thumbnail", ended.thumbnail_jpeg),
    ):
        name = f"event-{number}-{kind}.jpg"
        (directory / name).write_bytes(data)
        files[kind] = {"path": f"{camera}/{name}", "size_bytes": len(data)}
    clip = None
    if ended.clip is not None:
        name = f"event-{number}.webm"
        (directory / name).write_bytes(ended.clip.data)
        clip = {"path": f"{camera}/{name}", "size_bytes": len(ended.clip.data)}
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "peak_area_ratio": round(event.peak_area_ratio, 4),
        "motion_frames": event.motion_frames,
        "person": event.person,
        "person_confidence": event.person_confidence,
        "alert": event.alert,
        "boxes": [
            {"x": box.x, "y": box.y, "width": box.width, "height": box.height}
            for box in ended.boxes
        ],
        "snapshot_at": round(offset(ended.snapshot_at or event.started_at), 3),
        "snapshots": files,
        "clip": clip,
    }


class _CountingSource:
    """A source that counts its reads (the export's clock) and stops after ``limit``."""

    def __init__(self, source: VideoFileSource, *, limit: int) -> None:
        self._source = source
        self._limit = limit
        self.reads = 0

    @property
    def name(self) -> str:
        return self._source.name

    @property
    def fps(self) -> float | None:
        return self._source.fps

    @property
    def resolution(self) -> tuple[int, int] | None:
        return self._source.resolution

    def open(self) -> None:
        self._source.open()

    def read(self) -> Frame:
        if self.reads >= self._limit:
            raise SourceStoppedError
        frame = self._source.read()
        self.reads += 1
        return frame

    def close(self) -> None:
        self._source.close()


class _RecordingSink:
    def __init__(self) -> None:
        self.frames: list[FramePacket] = []
        self.ended: list[MotionEndedEvent] = []
        self.done = threading.Event()

    @property
    def has_viewers(self) -> bool:
        return True  # every analysed frame, with its detections

    def publish_frame(self, packet: FramePacket) -> None:
        self.frames.append(packet)

    def publish_event(self, event: CameraEvent) -> None:
        if isinstance(event, MotionEndedEvent):
            self.ended.append(event)

    def worker_exited(self, *, crashed: bool) -> None:
        self.done.set()


def _loop_shape(path: Path) -> tuple[float, int]:
    capture = cv2.VideoCapture(str(path))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        length = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        capture.release()
    if fps <= 0 or length <= 0:
        msg = f"{path} is not a readable video; run `vision-hub demo-footage` first"
        raise ValueError(msg)
    return fps, length


def _frame_size(path: Path) -> tuple[int, int]:
    capture = cv2.VideoCapture(str(path))
    try:
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return height, int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    finally:
        capture.release()
