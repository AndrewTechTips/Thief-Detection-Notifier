"""MJPEG (``multipart/x-mixed-replace``) live streams: one JPEG per part, newest frame wins.

Works in a plain ``<img src>`` tag. The stream counts as a viewer, so the camera worker
encodes every analysed frame while it is open.
"""

import asyncio
import time
from collections.abc import AsyncGenerator, Callable

from vision_hub.vision.bridge import LatestFrame

BOUNDARY = "frame"
MEDIA_TYPE = f"multipart/x-mixed-replace; boundary={BOUNDARY}"


def part(jpeg: bytes) -> bytes:
    header = (
        f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
    ).encode()
    return header + jpeg + b"\r\n"


async def mjpeg_stream(
    frames: LatestFrame,
    *,
    max_fps: float,
    still_running: Callable[[], bool],
    stall_timeout: float = 5.0,
) -> AsyncGenerator[bytes]:
    """Yield parts until the camera stops (checked whenever no frame arrives for
    ``stall_timeout`` seconds). Client disconnects cancel the generator from outside."""
    interval = 1.0 / max_fps
    last_sequence: int | None = None
    async with frames.watching():
        while True:
            started = time.monotonic()
            try:
                packet = await asyncio.wait_for(frames.next(last_sequence), stall_timeout)
            except TimeoutError:
                if still_running():
                    continue
                return
            last_sequence = packet.sequence
            yield part(packet.jpeg)
            if (wait := interval - (time.monotonic() - started)) > 0:
                await asyncio.sleep(wait)
