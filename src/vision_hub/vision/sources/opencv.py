"""Sources backed by ``cv2.VideoCapture``: local webcams, RTSP cameras and video files."""

import os
import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import cv2
import numpy as np

from vision_hub.vision.frame import Frame
from vision_hub.vision.sources.base import Pacer, SourceError
from vision_hub.vision.sources.config import (
    RtspSourceConfig,
    VideoFileSourceConfig,
    WebcamSourceConfig,
)


class Capture(Protocol):
    """The subset of ``cv2.VideoCapture`` used here (lets tests substitute a fake)."""

    def isOpened(self) -> bool: ...  # noqa: N802 - OpenCV's name

    def read(self) -> tuple[bool, Any]: ...

    def get(self, prop_id: int) -> float: ...

    def set(self, prop_id: int, value: float) -> bool: ...

    def release(self) -> None: ...


type CaptureFactory = Callable[..., Capture]


def _quiet_opencv() -> None:
    """OpenCV writes warnings straight to stderr, bypassing structured logging."""
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)


def _default_capture(*args: Any) -> Capture:
    capture: Capture = cv2.VideoCapture(*args)
    return capture


class _OpenCvSource:
    def __init__(self, capture_factory: CaptureFactory) -> None:
        self._capture_factory = capture_factory
        self._capture: Capture | None = None

    @property
    def name(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    @property
    def fps(self) -> float | None:
        if self._capture is None:
            return None
        fps = self._capture.get(cv2.CAP_PROP_FPS)
        return fps if fps > 0 else None

    @property
    def resolution(self) -> tuple[int, int] | None:
        if self._capture is None:
            return None
        width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return (width, height) if width > 0 and height > 0 else None

    def _open_capture(self, *args: Any) -> Capture:
        self.close()
        _quiet_opencv()
        capture = self._capture_factory(*args)
        if not capture.isOpened():
            capture.release()
            msg = f"cannot open {self.name}"
            raise SourceError(msg)
        self._capture = capture
        return capture

    def _grab(self) -> Frame | None:
        if self._capture is None:
            msg = f"{self.name} is not open"
            raise SourceError(msg)
        ok, frame = self._capture.read()
        if not ok or frame is None:
            return None
        return np.asarray(frame, dtype=np.uint8)

    def read(self) -> Frame:
        frame = self._grab()
        if frame is None:
            msg = f"{self.name} stopped delivering frames"
            raise SourceError(msg)
        return frame

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class WebcamSource(_OpenCvSource):
    def __init__(
        self, config: WebcamSourceConfig, *, capture_factory: CaptureFactory = _default_capture
    ) -> None:
        super().__init__(capture_factory)
        self._config = config

    @property
    def name(self) -> str:
        return f"webcam {self._config.index}"

    def open(self) -> None:
        capture = self._open_capture(self._config.index)
        requested = (
            (cv2.CAP_PROP_FRAME_WIDTH, self._config.width),
            (cv2.CAP_PROP_FRAME_HEIGHT, self._config.height),
            (cv2.CAP_PROP_FPS, self._config.fps),
        )
        for prop, value in requested:
            if value is not None:
                capture.set(prop, value)  # best effort: drivers may pick the nearest mode


# TCP avoids the smeared frames UDP packet loss causes. A 1 s probe (FFmpeg defaults to 5 s)
# makes opens take <1 s instead of ~3 s, keeping them well inside the open timeout.
FFMPEG_CAPTURE_OPTIONS = "rtsp_transport;tcp|analyzeduration;1000000|probesize;1000000"


def _quiet_ffmpeg() -> None:
    """FFmpeg logs failing stream URLs, which include credentials, to stderr: silence it.
    Operator-provided capture options take precedence over ours."""
    os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", FFMPEG_CAPTURE_OPTIONS)


class RtspSource(_OpenCvSource):
    def __init__(
        self,
        config: RtspSourceConfig,
        *,
        capture_factory: CaptureFactory = _default_capture,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(capture_factory)
        self._config = config
        self._clock = clock

    @property
    def name(self) -> str:
        return f"stream {self._config.url}"  # validated to contain no credentials

    def open(self) -> None:
        _quiet_ffmpeg()
        params: Sequence[int] = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
            int(self._config.open_timeout_seconds * 1000),
            cv2.CAP_PROP_READ_TIMEOUT_MSEC,
            int(self._config.read_timeout_seconds * 1000),
        ]
        url = self._config.connection_url().get_secret_value()
        started = self._clock()
        self._open_capture(url, cv2.CAP_FFMPEG, params)
        # If FFmpeg's open timeout fired, OpenCV may still report success and hand out the
        # frames buffered so far, but every read after that fails. Treat it as a failed open.
        if self._clock() - started >= self._config.open_timeout_seconds * 0.95:
            self.close()
            msg = (
                f"{self.name} took longer than {self._config.open_timeout_seconds:g} s to open; "
                "increase open_timeout_seconds if the camera is slow"
            )
            raise SourceError(msg)


class VideoFileSource(_OpenCvSource):
    """Plays a file at its native frame rate, optionally looping, like a camera would."""

    DEFAULT_FPS = 10.0

    def __init__(
        self,
        config: VideoFileSourceConfig,
        *,
        paced: bool = True,
        capture_factory: CaptureFactory = _default_capture,
    ) -> None:
        super().__init__(capture_factory)
        self._config = config
        self._paced = paced
        self._pacer: Pacer | None = None

    @property
    def name(self) -> str:
        return f"file {self._config.path.name}"

    def open(self) -> None:
        if not self._config.path.is_file():
            msg = f"{self.name} does not exist"
            raise SourceError(msg)
        self._open_capture(str(self._config.path))
        if self._paced:
            self._pacer = Pacer(self.fps or self.DEFAULT_FPS)

    def read(self) -> Frame:
        if self._pacer is not None:
            self._pacer.wait()
        frame = self._grab()
        if frame is None and self._config.loop and self._capture is not None:
            self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            frame = self._grab()
        if frame is None:
            msg = f"{self.name} has no more frames"
            raise SourceError(msg)
        return frame
