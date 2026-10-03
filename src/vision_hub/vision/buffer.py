"""Bounded ring buffer of recent frames, kept so an event clip can include the seconds *before*
motion was detected (Phase 3). Frames are stored downscaled to bound memory."""

import math
from collections import deque
from collections.abc import Iterator
from datetime import datetime

from vision_hub.vision.frame import Frame, resize_to_width


class PreRollBuffer:
    def __init__(self, *, seconds: float, fps: float, width: int = 320) -> None:
        if seconds <= 0 or fps <= 0 or width <= 0:
            msg = "seconds, fps and width must be positive"
            raise ValueError(msg)
        self._width = width
        self._frames: deque[tuple[datetime, Frame]] = deque(maxlen=math.ceil(seconds * fps))

    @property
    def capacity(self) -> int:
        return self._frames.maxlen or 0

    def add(self, frame: Frame, at: datetime) -> None:
        small, scale = resize_to_width(frame, self._width)
        # resize_to_width returns the input itself when no resize is needed; never alias it.
        self._frames.append((at, small.copy() if scale == 1.0 else small))

    def __iter__(self) -> Iterator[tuple[datetime, Frame]]:
        return iter(tuple(self._frames))

    def __len__(self) -> int:
        return len(self._frames)

    def clear(self) -> None:
        self._frames.clear()
