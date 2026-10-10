"""Demo cameras from real footage: free stock clips, fetched from their publisher (pinned by
SHA-256) and turned into seamless camera loops on this machine, so the project never re-hosts
them. Sources, credits and licence: docs/demo-footage.md.

Each loop is the clip, a crossfade from its last frame back to its first (a hard cut would look
like motion to the detector), then the empty scene at its start played back and forth for a
while, so visits come every half minute or so instead of back to back. Loops are WebM (VP8,
written by OpenCV like the event clips), 640 px wide at 15 fps, no wider than 16:9.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from vision_hub.core.downloads import fetch_verified
from vision_hub.vision.frame import Frame, resize_to_width

FPS = 15.0
WIDTH = 640
CROSSFADE_SECONDS = 1.5
_FOURCC = cv2.VideoWriter.fourcc(*"VP80")


@dataclass(frozen=True, slots=True)
class DemoClip:
    name: str  # the loop is <directory>/<name>.webm
    title: str
    author: str
    page: str  # where the clip is published (credit)
    url: str
    sha256: str
    end: float  # seconds of the source used, from its start
    idle: float  # how much of the (empty) start is the idle scene...
    idle_seconds: float  # ...played back and forth for this long between visits


CLIPS = (
    DemoClip(
        name="front-door",
        title="Man Pressing the Doorbell",
        author="MART PRODUCTION",
        page="https://www.pexels.com/video/man-pressing-the-doorbell-7701952/",
        url="https://videos.pexels.com/video-files/7701952/7701952-hd_1366_720_25fps.mp4",
        sha256="a362aef82f6070fa01c0012755a7e5ccd9b69a3b5903055ec29b3f0ee2ef5247",
        end=18.5,
        idle=2.0,
        idle_seconds=12.0,
    ),
    DemoClip(
        name="lobby",
        title="Man in Shirt Walking in Corridor",
        author="Jonathan Khoo",
        page="https://www.pexels.com/video/man-in-shirt-walking-in-corridor-11903981/",
        url="https://videos.pexels.com/video-files/11903981/11903981-hd_1280_720_25fps.mp4",
        sha256="d54b0e938a9bba2192dc7f1094efc8fdf4326d7b158b20d5e8d2152200c225b5",
        end=19.0,
        idle=6.0,
        idle_seconds=6.0,
    ),
    DemoClip(
        name="driveway",
        title="SUV Exits Modern Residential Garage at Dusk",
        author="dp singh Bhullar",
        page="https://www.pexels.com/video/suv-exits-modern-residential-garage-at-dusk-29734450/",
        url="https://videos.pexels.com/video-files/29734450/12782485_640_360_24fps.mp4",
        sha256="f157bfd3f1c1f514268a7bea3ef45bc8d30b5bd2511681c2f461b49804a9ba1d",
        end=34.0,  # the source cuts to another shot at 35 s
        idle=2.0,
        idle_seconds=10.0,
    ),
    DemoClip(
        name="patio",
        title="Palm Tree Shadow on Modern Building Exterior",
        author="Nothing Ahead",
        page="https://www.pexels.com/video/palm-tree-shadow-on-modern-building-exterior-35084306/",
        url="https://videos.pexels.com/video-files/35084306/14863146_640_360_60fps.mp4",
        sha256="d14781dc19f92ab87b7303fba301bb8f2fc859bd13e22941ff6779f56311a916",
        end=21.0,
        idle=0.0,  # the shadow never stops moving: no idle scene
        idle_seconds=0.0,
    ),
)


def prepare_footage(
    directory: Path, clips: Sequence[DemoClip] = CLIPS, *, fetch: bool = True
) -> list[tuple[DemoClip, bool]]:
    """Makes every missing loop in ``directory``, fetching its source into ``sources/`` first
    (skipped with ``fetch=False``, for sources put there by hand). Returns each clip and
    whether its loop was made now (False: it was already there)."""
    made = []
    for clip in clips:
        loop = directory / f"{clip.name}.webm"
        if loop.is_file():
            made.append((clip, False))
            continue
        source = directory / "sources" / f"{clip.name}.mp4"
        if fetch:
            fetch_verified(source, url=clip.url, sha256=clip.sha256)
        make_loop(source, clip, loop)
        made.append((clip, True))
    return made


def make_loop(source: Path, clip: DemoClip, output: Path) -> None:
    """Writes the loop next to ``output`` and moves it into place once complete."""
    frames = _frames(source, clip.end)
    first = next(frames, None)
    if first is None:
        msg = f"{source} has no readable frames"
        raise ValueError(msg)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}")
    height, width = first.shape[:2]
    writer = cv2.VideoWriter(str(temporary), _FOURCC, FPS, (width, height))
    try:
        if not writer.isOpened():  # pragma: no cover - VP8 is in every OpenCV build we use
            msg = "OpenCV cannot write WebM (VP8) here"
            raise RuntimeError(msg)
        idle = [first]
        idle_frames = round(clip.idle * FPS)
        last = first
        writer.write(first)
        for frame in frames:
            if len(idle) < idle_frames:
                idle.append(frame)
            writer.write(frame)
            last = frame
        for frame in crossfade(last, first, round(CROSSFADE_SECONDS * FPS)):
            writer.write(frame)
        for frame in back_and_forth(idle, round(clip.idle_seconds * FPS)):
            writer.write(frame)
    finally:
        writer.release()
    temporary.replace(output)


def crossfade(start: Frame, end: Frame, steps: int) -> Iterator[Frame]:
    """``steps`` frames blending from ``start`` to ``end``, the last one being ``end``."""
    for step in range(1, steps + 1):
        weight = step / steps
        yield np.asarray(cv2.addWeighted(start, 1 - weight, end, weight, 0), np.uint8)


def back_and_forth(frames: Sequence[Frame], count: int) -> Iterator[Frame]:
    """About ``count`` frames going forward and back through ``frames``, ending on the first
    one (where the loop starts again). Never repeats a frame twice in a row."""
    if count <= 0 or len(frames) < 2:
        return
    cycle = [*frames[1:], *frames[-2::-1]]  # 1 .. n-1, then n-2 .. 0
    for _ in range(max(1, round(count / len(cycle)))):
        yield from cycle


def _frames(path: Path, end: float) -> Iterator[Frame]:
    """The first ``end`` seconds of the video at ``FPS`` (skipping or repeating frames as
    needed), ``WIDTH`` pixels wide."""
    capture = cv2.VideoCapture(str(path))
    try:
        source_fps = capture.get(cv2.CAP_PROP_FPS) or FPS
        due = 0.0
        index = 0
        while True:
            ok, frame = capture.read()
            at = index / source_fps
            index += 1
            if not ok or at > end:
                return
            if at + 1e-6 < due:
                continue  # a faster source: skip frames
            small = resize_to_width(_widescreen(np.asarray(frame, np.uint8)), WIDTH)[0]
            small = small[: small.shape[0] // 2 * 2]  # VP8 wants an even height
            while at + 1e-6 >= due:  # a slower source: repeat frames
                due += 1 / FPS
                yield small
    finally:
        capture.release()


def _widescreen(frame: Frame) -> Frame:
    """Crops a frame wider than 16:9 to 16:9 around its centre, so every demo camera has the
    same shape in the live grid."""
    height, width = frame.shape[:2]
    target = round(height * 16 / 9)
    if width <= target:
        return frame
    left = (width - target) // 2
    return frame[:, left : left + target]
