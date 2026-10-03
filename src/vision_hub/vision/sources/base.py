"""The interface every video source implements, plus shared helpers."""

import time
from collections.abc import Callable
from typing import Protocol

from vision_hub.vision.frame import Frame


class SourceError(Exception):
    """The source cannot be opened or stopped delivering frames. Usually transient."""


class SourceStoppedError(Exception):
    """Raised from a blocking call when the owner asked the source to stop."""


class FrameSource(Protocol):
    """A blocking frame producer, driven by one camera worker thread.

    ``read`` returns the next frame or raises ``SourceError``. Live sources (cameras) deliver
    frames at their own rate; file and synthetic sources pace themselves to ``fps`` so they
    behave like a camera.
    """

    @property
    def name(self) -> str:
        """Human-readable and safe to log: never contains credentials."""
        ...

    @property
    def fps(self) -> float | None: ...

    @property
    def resolution(self) -> tuple[int, int] | None:
        """``(width, height)`` once open."""
        ...

    def open(self) -> None: ...

    def read(self) -> Frame: ...

    def close(self) -> None:
        """Release the device. Idempotent."""
        ...


type MonotonicClock = Callable[[], float]
type Sleeper = Callable[[float], None]


class Pacer:
    """Spaces calls ``1/fps`` apart, without drifting when work takes a variable time."""

    def __init__(
        self, fps: float, *, clock: MonotonicClock = time.monotonic, sleep: Sleeper = time.sleep
    ) -> None:
        self._interval = 1.0 / fps
        self._clock = clock
        self._sleep = sleep
        self._next: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._next is None or now - self._next > self._interval:
            self._next = now  # first frame, or we fell far behind: don't try to catch up
        elif self._next > now:
            self._sleep(self._next - now)
        self._next += self._interval

    def reset(self) -> None:
        self._next = None
