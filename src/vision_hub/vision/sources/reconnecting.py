"""Keeps any source alive across disconnects, with exponential backoff and jitter."""

import threading
from collections.abc import Callable

from vision_hub.core.backoff import Backoff
from vision_hub.core.logging import get_logger
from vision_hub.domain.devices import DeviceStatus
from vision_hub.vision.frame import Frame
from vision_hub.vision.sources.base import FrameSource, SourceError, SourceStoppedError

logger = get_logger(__name__)

type StatusCallback = Callable[[DeviceStatus], None]


class ReconnectingSource:
    """Wraps a source: failed opens and reads trigger reconnect attempts until it works again,
    ``max_attempts`` is exhausted (status ``FAILED``), or ``stop`` is set.

    The source counts as ``ONLINE`` (and the backoff resets) only once a frame has actually been
    read: some streams open "successfully" and then fail on the first read, and treating that as
    recovery would retry at the minimum delay forever.

    Backoff waits use ``stop.wait()``, so stopping a camera takes effect immediately even in the
    middle of a 30 s delay.
    """

    def __init__(
        self,
        source: FrameSource,
        *,
        stop: threading.Event,
        backoff: Backoff | None = None,
        on_status: StatusCallback | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self._source = source
        self._stop = stop
        self._backoff = backoff or Backoff()
        self._on_status = on_status or (lambda _status: None)
        self._max_attempts = max_attempts
        self._awaiting_first_frame = False

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
        self._on_status(DeviceStatus.STARTING)
        try:
            self._source.open()
        except SourceError as exc:
            logger.warning("source_open_failed", source=self.name, error=str(exc))
            self._reconnect()
            return
        self._awaiting_first_frame = True

    def read(self) -> Frame:
        while True:
            if self._stop.is_set():
                raise SourceStoppedError
            try:
                frame = self._source.read()
            except SourceError as exc:
                logger.warning("source_read_failed", source=self.name, error=str(exc))
                self._reconnect()
                continue
            if self._awaiting_first_frame:
                self._awaiting_first_frame = False
                self._backoff.reset()
                self._on_status(DeviceStatus.ONLINE)
            return frame

    def close(self) -> None:
        self._source.close()

    def _reconnect(self) -> None:
        self._source.close()
        self._on_status(DeviceStatus.RECONNECTING)
        attempts = 0
        while True:
            delay = self._backoff.next_delay()
            if self._stop.wait(delay):
                raise SourceStoppedError
            attempts += 1
            try:
                self._source.open()
            except SourceError as exc:
                logger.info(
                    "source_reconnect_failed", source=self.name, attempt=attempts, error=str(exc)
                )
                if self._max_attempts is not None and attempts >= self._max_attempts:
                    self._on_status(DeviceStatus.FAILED)
                    raise
                continue
            logger.info("source_reopened", source=self.name, attempts=attempts)
            self._awaiting_first_frame = True
            return
