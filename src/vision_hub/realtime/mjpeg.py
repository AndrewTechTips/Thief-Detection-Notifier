"""MJPEG (``multipart/x-mixed-replace``) live streams: one JPEG per part, newest frame wins.

Works in a plain ``<img src>`` tag. The stream counts as a viewer, so the camera worker
encodes every analysed frame while it is open.

Frames are clean; a part showing motion carries what was detected on it in an
``X-Detections`` header (AD-23), so a viewer that reads the parts itself can draw boxes that
match the picture exactly. An ``<img>`` ignores it.
"""

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Callable

from vision_hub.vision.bridge import FramePacket, FramesClosedError, LatestFrame

BOUNDARY = "frame"
MEDIA_TYPE = f"multipart/x-mixed-replace; boundary={BOUNDARY}"
DETECTIONS_HEADER = "X-Detections"


def part(jpeg: bytes, detections: str | None = None) -> bytes:
    extra = f"{DETECTIONS_HEADER}: {detections}\r\n" if detections else ""
    header = (
        f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n{extra}\r\n"
    ).encode()
    return header + jpeg + b"\r\n"


def detections(packet: FramePacket) -> str | None:
    """Compact JSON of a frame's detections, or None when there is nothing to draw.

    ``{"boxes": [[x, y, width, height], ...], "person": 0.87}``: boxes in fractions of the
    picture (0..1), ``person`` the score once a person was found in the open event, else null.
    """
    if not packet.boxes and packet.person is None:
        return None
    width, height = packet.width, packet.height
    boxes = [
        [
            round(box.x / width, 4),
            round(box.y / height, 4),
            round(box.width / width, 4),
            round(box.height / height, 4),
        ]
        for box in packet.boxes
    ]
    person = None if packet.person is None else round(packet.person, 2)
    return json.dumps({"boxes": boxes, "person": person}, separators=(",", ":"))


async def mjpeg_stream(
    frames: LatestFrame,
    *,
    max_fps: float,
    still_running: Callable[[], bool],
    stall_timeout: float = 5.0,
) -> AsyncGenerator[bytes]:
    """Yield parts until the camera stops (checked whenever no frame arrives for
    ``stall_timeout`` seconds) or the hub shuts down. Client disconnects cancel the generator
    from outside."""
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
            except FramesClosedError:
                return
            last_sequence = packet.sequence
            yield part(packet.jpeg, detections(packet))
            if (wait := interval - (time.monotonic() - started)) > 0:
                await asyncio.sleep(wait)
