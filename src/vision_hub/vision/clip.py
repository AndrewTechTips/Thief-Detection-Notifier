"""Event clips: the seconds before motion was detected, then the event itself, as VP8 video in
WebM (AD-21). Runs in the camera thread. Encoding costs about 5 ms per 640 px frame and happens
only while an event is open; between events, frames only go into the pre-roll buffer.

Frames keep their real timing: the video runs at the analysis rate, and a frame is repeated
when the camera fell behind, so a clip lasts as long as what it shows.
"""

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from vision_hub.domain.events import VideoClip
from vision_hub.vision.buffer import PreRollBuffer
from vision_hub.vision.frame import Frame, resize_to_width

CONTENT_TYPE = "video/webm"
_SUFFIX = ".webm"
_FOURCC = cv2.VideoWriter.fourcc(*"VP80")
# A longer gap (a camera reconnecting) is not filled with a frozen frame: the clip jumps.
_MAX_GAP = timedelta(seconds=2)


class ClipError(RuntimeError):
    """The clip could not be written (no VP8 encoder, disk full)."""


@dataclass(frozen=True, slots=True)
class ClipSettings:
    pre_roll_seconds: float = 3.0
    max_seconds: float = 120.0
    width: int = 640


class ClipRecorder:
    """One per camera worker; not thread-safe. Call ``add`` with every analysed frame,
    ``start`` when motion starts and ``finish`` when it ends."""

    def __init__(self, settings: ClipSettings, *, fps: float) -> None:
        self._settings = settings
        self._fps = fps
        self._pre_roll = (
            PreRollBuffer(seconds=settings.pre_roll_seconds, fps=fps, width=settings.width)
            if settings.pre_roll_seconds > 0
            else None
        )
        self._recording = False
        self._writer: cv2.VideoWriter | None = None
        self._path: Path | None = None
        self._size: tuple[int, int] | None = None  # (width, height) of the video
        self._first_at: datetime | None = None
        self._last_at: datetime | None = None
        self._written = 0  # frames in the file, repeats included

    @property
    def recording(self) -> bool:
        return self._recording

    def add(self, frame: Frame, at: datetime) -> None:
        if self._recording:
            self._write(frame, at)
        elif self._pre_roll is not None:
            self._pre_roll.add(frame, at)

    def start(self) -> None:
        """Begins a clip with the frames from before the motion was detected."""
        self.abort()
        self._recording = True
        if self._pre_roll is not None:
            earlier = list(self._pre_roll)
            self._pre_roll.clear()
            for at, frame in earlier:
                self._write(frame, at)

    def finish(self) -> VideoClip | None:
        """Closes the clip and returns it (None if nothing was written)."""
        if not self._recording:
            return None
        writer, path, size, written = self._writer, self._path, self._size, self._written
        self._writer = None
        try:
            if writer is None or path is None or size is None or written == 0:
                return None
            writer.release()
            data = path.read_bytes()
            if not data:
                msg = "the video encoder wrote an empty file"
                raise ClipError(msg)
            return VideoClip(
                data=data,
                content_type=CONTENT_TYPE,
                duration_seconds=written / self._fps,
                width=size[0],
                height=size[1],
            )
        finally:
            self.abort()

    def abort(self) -> None:
        """Drops the clip in progress, if any, and its temporary file."""
        if self._writer is not None:
            self._writer.release()
        if self._path is not None:
            self._path.unlink(missing_ok=True)
        self._recording = False
        self._writer = None
        self._path = None
        self._size = None
        self._first_at = None
        self._last_at = None
        self._written = 0

    def _write(self, frame: Frame, at: datetime) -> None:
        if self._first_at is not None and at - self._first_at > timedelta(
            seconds=self._settings.max_seconds
        ):
            return  # long enough: the rest of the event is in the snapshots and the history
        image = self._fit(frame)
        writer = self._writer or self._open()
        for _ in range(self._repeats(at)):
            writer.write(image)
            self._written += 1
        self._last_at = at
        self._first_at = self._first_at or at

    def _repeats(self, at: datetime) -> int:
        """How often to write this frame so the video keeps time with the camera."""
        if self._last_at is None:
            return 1
        gap = at - self._last_at
        if gap > _MAX_GAP:
            return 1
        return max(1, round(gap.total_seconds() * self._fps))

    def _fit(self, frame: Frame) -> Frame:
        """BGR at the clip's size. Pre-roll frames are already small; a camera that changes
        resolution mid-event is scaled to the size the clip started with."""
        if frame.ndim == 2:
            frame = np.asarray(cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR), np.uint8)
        small, _ = resize_to_width(frame, self._settings.width)
        if self._size is None:
            # VP8 stores chroma at half resolution: keep both sides even.
            width, height = small.shape[1] & ~1, small.shape[0] & ~1
            self._size = (max(width, 2), max(height, 2))
        if (small.shape[1], small.shape[0]) != self._size:
            small = np.asarray(
                cv2.resize(small, self._size, interpolation=cv2.INTER_AREA), np.uint8
            )
        return small

    def _open(self) -> cv2.VideoWriter:
        if self._size is None:  # pragma: no cover - _fit sets it before any write
            msg = "clip size unknown"
            raise ClipError(msg)
        fd, name = tempfile.mkstemp(prefix="vision-hub-clip-", suffix=_SUFFIX)
        os.close(fd)
        self._path = Path(name)
        writer = cv2.VideoWriter(str(self._path), _FOURCC, self._fps, self._size)
        if not writer.isOpened():
            msg = "this OpenCV build cannot write VP8/WebM video"
            raise ClipError(msg)
        self._writer = writer
        return writer
