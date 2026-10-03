"""Hand-off between camera worker threads and the asyncio event loop.

Workers never touch asyncio objects directly; they call ``LoopBridge`` methods, which schedule
work on the loop with ``call_soon_threadsafe``:

* frames: latest-frame-wins with at most one pending callback per camera, so a busy loop sees
  the newest frame instead of an ever-growing backlog;
* events: delivered one callback each, never dropped (they are rare: a few per minute).
"""

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime

from vision_hub.domain.events import CameraEvent


@dataclass(frozen=True, slots=True)
class FramePacket:
    """One encoded live-view frame."""

    device_id: str
    sequence: int
    captured_at: datetime
    width: int
    height: int
    motion: bool
    jpeg: bytes = field(repr=False)


class LatestFrame:
    """The newest frame of one camera, for any number of async consumers. Loop thread only."""

    def __init__(self) -> None:
        self._packet: FramePacket | None = None
        self._changed = asyncio.Event()
        self._viewers = 0

    @property
    def latest(self) -> FramePacket | None:
        return self._packet

    @property
    def viewers(self) -> int:
        """Read by the worker thread to decide whether to encode every frame."""
        return self._viewers

    def publish(self, packet: FramePacket) -> None:
        self._packet = packet
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    async def next(self, after_sequence: int | None = None) -> FramePacket:
        """Wait for a frame newer than ``after_sequence`` (or any frame, if None)."""
        while (packet := self._packet) is None or (
            after_sequence is not None and packet.sequence <= after_sequence
        ):
            await self._changed.wait()
        return packet

    @asynccontextmanager
    async def watching(self) -> AsyncIterator[LatestFrame]:
        """Register a live viewer for the duration of the block (e.g. an MJPEG stream)."""
        self._viewers += 1
        try:
            yield self
        finally:
            self._viewers -= 1


class LoopBridge:
    """Thread-safe sink a ``CameraWorker`` publishes into. Create it in the loop thread."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        frames: LatestFrame,
        on_event: Callable[[CameraEvent], None],
        on_exit: Callable[[bool], None],
    ) -> None:
        self._loop = loop
        self._frames = frames
        self._on_event = on_event
        self._on_exit = on_exit
        self._lock = threading.Lock()
        self._pending_frame: FramePacket | None = None

    @property
    def has_viewers(self) -> bool:
        return self._frames.viewers > 0

    def publish_frame(self, packet: FramePacket) -> None:
        with self._lock:
            already_scheduled = self._pending_frame is not None
            self._pending_frame = packet
        if not already_scheduled:
            self._call_soon(self._deliver_frame)

    def publish_event(self, event: CameraEvent) -> None:
        self._call_soon(self._on_event, event)

    def worker_exited(self, *, crashed: bool) -> None:
        self._call_soon(self._on_exit, crashed)

    def _deliver_frame(self) -> None:
        with self._lock:
            packet, self._pending_frame = self._pending_frame, None
        if packet is not None:  # pragma: no branch - one callback per pending frame
            self._frames.publish(packet)

    def _call_soon[*Ts](self, callback: Callable[[*Ts], object], *args: *Ts) -> None:
        # RuntimeError: the loop already closed during shutdown; nothing is listening any more.
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(callback, *args)
