"""A generated "room" a figure walks through periodically: demos and tests without hardware.

Motion is a function of the frame number, not wall-clock time, so a given configuration always
produces the same sequence of events.
"""

import cv2
import numpy as np
import numpy.typing as npt

from vision_hub.vision.frame import Frame
from vision_hub.vision.sources.base import Pacer, SourceError
from vision_hub.vision.sources.config import SyntheticSourceConfig

_NOISE_SIGMA = 4.0


class SyntheticSource:
    def __init__(self, config: SyntheticSourceConfig, *, paced: bool = True) -> None:
        self._config = config
        self._paced = paced
        self._pacer: Pacer | None = None
        self._rng = np.random.default_rng(config.seed)
        self._room: npt.NDArray[np.float32] | None = None
        self._frame_number = 0

    @property
    def name(self) -> str:
        return f"synthetic {self._config.width}x{self._config.height}"

    @property
    def fps(self) -> float:
        return self._config.fps

    @property
    def resolution(self) -> tuple[int, int] | None:
        return (self._config.width, self._config.height) if self._room is not None else None

    def open(self) -> None:
        self._room = self._draw_room()
        self._frame_number = 0
        self._pacer = Pacer(self._config.fps) if self._paced else None

    def read(self) -> Frame:
        room = self._room
        if room is None:
            msg = f"{self.name} is not open"
            raise SourceError(msg)
        if self._pacer is not None:
            self._pacer.wait()
        scene = room + self._rng.normal(0, _NOISE_SIGMA, room.shape).astype(np.float32)
        if (position := self._visitor_position()) is not None:
            self._draw_visitor(scene, position)
        self._frame_number += 1
        gray = np.clip(scene, 0, 255).astype(np.uint8)
        return np.asarray(cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), dtype=np.uint8)

    def close(self) -> None:
        self._room = None

    def is_visiting(self, frame_number: int) -> bool:
        """Whether a figure is in view on ``frame_number`` (0-based)."""
        period = round(self._config.visit_every_seconds * self._config.fps)
        visit = round(self._config.visit_seconds * self._config.fps)
        offset = frame_number % period
        start = period - visit  # each period begins empty, so the background can settle
        return offset >= start

    def _visitor_position(self) -> float | None:
        """Horizontal progress 0..1 of the current visit, or None when the room is empty."""
        if not self.is_visiting(self._frame_number):
            return None
        period = round(self._config.visit_every_seconds * self._config.fps)
        visit = round(self._config.visit_seconds * self._config.fps)
        return ((self._frame_number % period) - (period - visit)) / max(visit - 1, 1)

    def _draw_room(self) -> npt.NDArray[np.float32]:
        width, height = self._config.width, self._config.height
        yy, xx = np.mgrid[0:height, 0:width]
        room: npt.NDArray[np.float32] = np.asarray(
            60 + 80 * xx / width + 30 * yy / height, dtype=np.float32
        )
        room[int(height * 0.62) :, :] -= 25  # floor
        door = (int(width * 0.7), int(height * 0.2), int(width * 0.9), int(height * 0.62))
        cv2.rectangle(room, door[:2], door[2:], 150, -1)
        return room

    def _draw_visitor(self, scene: npt.NDArray[np.float32], progress: float) -> None:
        width, height = self._config.width, self._config.height
        x = int(width * (0.08 + 0.84 * progress))
        head, body_w, body_h = int(height * 0.07), int(width * 0.055), int(height * 0.4)
        top = int(height * 0.35)
        cv2.circle(scene, (x, top), head, 210, -1)
        cv2.rectangle(scene, (x - body_w, top + head), (x + body_w, top + head + body_h), 200, -1)
