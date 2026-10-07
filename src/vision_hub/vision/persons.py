"""Person detection (AD-22): YOLOX-s from OpenCV's model zoo (ONNX, Apache 2.0) on onnxruntime.

A check takes about 280 ms on one core of a typical CPU (45 ms on Apple silicon, which has
matrix units), far too long for a camera thread: the live view would stall. So checks run on
one background thread shared by every camera (``PersonChecker``), which also caps the whole
feature at one core however many cameras there are. A camera hands frames over without
waiting; while an event is open it checks when the motion starts, then every couple of
seconds, a few times at most, and the frame with the most motion gets a last look before the
event is published.

onnxruntime's telemetry (HTTPS uploads on Linux) is switched off before the library loads: a
home security hub should not contact anyone.
"""

import hashlib
import os
import tempfile
import threading
import urllib.request
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

import cv2
import numpy as np
import numpy.typing as npt

from vision_hub.core.logging import get_logger
from vision_hub.domain.motion import BoundingBox
from vision_hub.vision.frame import Frame

logger = get_logger(__name__)

MODEL_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
    "object_detection_yolox/object_detection_yolox_2022nov.onnx"
)
MODEL_SHA256 = "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063"
MODEL_BYTES = 35_858_002

INPUT_SIZE = 640
PERSON_CLASS = 0  # COCO
_PAD = 114  # YOLOX's letterbox grey
_NMS_IOU = 0.45
# Candidates below this never count; the configured threshold decides what "a person" is.
_FLOOR = 0.25

type AlertOn = Literal["motion", "person"]


@dataclass(frozen=True, slots=True)
class Person:
    box: BoundingBox  # in the frame's own coordinates
    confidence: float


class PersonDetector(Protocol):
    def detect(self, frame: Frame) -> Sequence[Person]:
        """People in the frame, most confident first. Thread-safe."""
        ...


@dataclass(frozen=True, slots=True)
class PersonVerdict:
    confidence: float | None  # best score; None when no check worked
    person: bool | None  # None when no check worked


class ModelError(RuntimeError):
    """The model is missing, damaged or cannot run."""


class YoloxPersonDetector:
    def __init__(self, path: Path, *, threads: int = 1) -> None:
        # Must be set before the library loads; an operator can still turn it on explicitly.
        os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
        import onnxruntime  # loaded only when person detection is used

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.log_severity_level = 3  # errors only: the zoo model's exporter warnings are noise
        try:
            self._session: Any = onnxruntime.InferenceSession(
                str(path), options, providers=["CPUExecutionProvider"]
            )
        except Exception as exc:  # onnxruntime raises its own exception types
            msg = f"cannot load the person model {path}: {exc}"
            raise ModelError(msg) from exc
        self._input = self._session.get_inputs()[0].name
        self._grids, self._strides = _grids(INPUT_SIZE)

    def detect(self, frame: Frame) -> Sequence[Person]:
        image = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        boxed, scale = letterbox(np.asarray(image, np.uint8), INPUT_SIZE)
        blob = boxed.transpose(2, 0, 1)[np.newaxis].astype(np.float32)
        output = np.asarray(self._session.run(None, {self._input: blob})[0][0], np.float32)
        return decode(output, self._grids, self._strides, scale, frame.shape[1], frame.shape[0])


class PersonChecker:
    """Runs every camera's person checks on one background thread."""

    def __init__(
        self,
        detector: PersonDetector,
        *,
        threshold: float = 0.5,
        check_interval_seconds: float = 2.0,
        max_checks: int = 5,
    ) -> None:
        self.detector = detector
        self.threshold = threshold
        self.interval = timedelta(seconds=check_interval_seconds)
        self.max_checks = max_checks
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="persons")

    def event(self, started_at: datetime) -> EventCheck:
        """Checks for one motion event; the first is due at once."""
        return EventCheck(self, started_at)

    def submit(self, task: Callable[[], None]) -> Future[None]:
        return self._executor.submit(task)

    def close(self) -> None:
        """Blocking: lets queued checks finish, so no ended event is lost at shutdown."""
        self._executor.shutdown(wait=True)


class EventCheck:
    """The person checks of one event. ``offer`` and ``finish`` are called from the camera
    thread and never wait; the checks themselves run on the checker's thread."""

    def __init__(self, checker: PersonChecker, started_at: datetime) -> None:
        self._checker = checker
        self._lock = threading.Lock()
        self._best: float | None = None
        self._failed = False
        self._checks = 0
        self._due = started_at
        self._pending: Future[None] | None = None

    @property
    def found(self) -> bool:
        with self._lock:
            return self._best is not None and self._best >= self._checker.threshold

    def offer(self, frame: Frame, at: datetime) -> None:
        """A frame from the open event: checked if a check is due and none is running."""
        if self.found or self._failed or self._checks >= self._checker.max_checks:
            return
        if at < self._due or (self._pending is not None and not self._pending.done()):
            return
        self._checks += 1
        self._due = at + self._checker.interval
        image = frame.copy()  # the camera thread moves on to the next frame
        self._pending = self._checker.submit(lambda: self._check(image))

    def finish(self, best_frame: Frame, done: Callable[[PersonVerdict], None]) -> None:
        """Gives the frame with the most motion a last look (unless a person was already
        found), then calls ``done`` with the verdict, on the checker's thread."""
        image = best_frame.copy()

        def final() -> None:
            if not self.found and not self._failed:
                self._check(image)
            done(self.verdict())

        self._checker.submit(final)  # after any check still running: one thread, in order

    def verdict(self) -> PersonVerdict:
        with self._lock:
            if self._failed or self._best is None:
                return PersonVerdict(confidence=None, person=None)
            return PersonVerdict(
                confidence=self._best, person=self._best >= self._checker.threshold
            )

    def _check(self, image: Frame) -> None:
        if self._failed:
            return
        try:
            people = self._checker.detector.detect(image)
        except Exception:
            logger.exception("person_check_failed")
            with self._lock:
                self._failed = True
            return
        score = people[0].confidence if people else 0.0
        with self._lock:
            self._best = max(self._best or 0.0, score)


def letterbox(image: Frame, size: int) -> tuple[Frame, float]:
    """Scales the image to fit ``size`` x ``size``, top-left aligned, padded with grey."""
    height, width = image.shape[:2]
    scale = min(size / height, size / width)
    resized = cv2.resize(
        image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_LINEAR
    )
    canvas = np.full((size, size, 3), _PAD, np.uint8)
    canvas[: resized.shape[0], : resized.shape[1]] = resized
    return canvas, scale


def _grids(size: int) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
    """Cell offsets and strides for YOLOX's three output scales (8, 16 and 32 px)."""
    grids, strides = [], []
    for stride in (8, 16, 32):
        cells = size // stride
        xs, ys = np.meshgrid(np.arange(cells), np.arange(cells))
        grids.append(np.stack((xs, ys), 2).reshape(-1, 2))
        strides.append(np.full((cells * cells, 1), stride))
    return (
        np.concatenate(grids).astype(np.float32),
        np.concatenate(strides).astype(np.float32),
    )


def decode(
    output: npt.NDArray[np.float32],
    grids: npt.NDArray[np.float32],
    strides: npt.NDArray[np.float32],
    scale: float,
    width: int,
    height: int,
) -> list[Person]:
    """YOLOX rows (cx, cy, w, h, objectness, 80 class scores) to people in frame coordinates."""
    scores = output[:, 4] * output[:, 5 + PERSON_CLASS]
    keep = scores >= _FLOOR
    if not keep.any():
        return []
    centres = (output[keep, :2] + grids[keep]) * strides[keep]
    sizes = np.exp(output[keep, 2:4]) * strides[keep]
    boxes = np.concatenate([centres - sizes / 2, sizes], axis=1) / scale
    kept = scores[keep]
    chosen = cv2.dnn.NMSBoxes(boxes.tolist(), kept.tolist(), _FLOOR, _NMS_IOU)
    people = []
    for index in np.array(chosen, dtype=int).flatten():
        x, y, w, h = boxes[index]
        left, top = max(0, round(float(x))), max(0, round(float(y)))
        right, bottom = min(width, round(float(x + w))), min(height, round(float(y + h)))
        if right > left and bottom > top:
            box = BoundingBox(x=left, y=top, width=right - left, height=bottom - top)
            people.append(Person(box=box, confidence=round(float(kept[index]), 4)))
    return sorted(people, key=lambda person: person.confidence, reverse=True)


def should_alert(alert_on: AlertOn, confidence: float | None, threshold: float) -> bool:
    """Whether an event raises an alert. ``confidence`` is None when nobody could check (no
    model, or it failed): then it alerts anyway, since a missed intruder costs more than a
    false alarm."""
    if alert_on == "motion" or confidence is None:
        return True
    return confidence >= threshold


def load_person_detector(path: Path, *, threads: int = 1) -> PersonDetector | None:
    """The detector, or None (logged) when the model file is missing or broken."""
    if not path.is_file():
        logger.warning(
            "person_model_missing",
            path=str(path),
            hint="run `vision-hub download-model`; until then every event alerts as motion",
        )
        return None
    try:
        detector = YoloxPersonDetector(path, threads=threads)
    except ModelError as exc:
        logger.error("person_model_unusable", path=str(path), error=str(exc))
        return None
    logger.info("person_detection_ready", path=str(path))
    return detector


def download_model(path: Path, *, url: str = MODEL_URL, sha256: str = MODEL_SHA256) -> bool:
    """Fetches the pinned model and checks its SHA-256. Returns False if it was already there.
    The file appears only once complete and verified."""
    if path.is_file() and _sha256(path) == sha256:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".download-", suffix=".onnx")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as file, urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https URL
            while chunk := response.read(1 << 20):
                file.write(chunk)
        if (actual := _sha256(temporary)) != sha256:
            msg = f"downloaded model has SHA-256 {actual}, expected {sha256}"
            raise ModelError(msg)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()
