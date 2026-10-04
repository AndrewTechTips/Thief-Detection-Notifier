"""Per-frame cost of each pipeline stage, and CPU per camera.

    uv run python benchmarks/pipeline.py

Frames come from the synthetic source (a figure walking through a noisy scene), generated up
front and replayed, so the numbers measure the hub and not the frame generator. Decoding
(RTSP/H.264, USB) is camera-specific and appears only as a JPEG-decode proxy.
"""

import argparse
import itertools
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial

import cv2

from vision_hub.domain.events import CameraEvent
from vision_hub.domain.motion import BoundingBox
from vision_hub.vision.annotate import draw_boxes
from vision_hub.vision.bridge import FramePacket
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.detector import MotionDetector
from vision_hub.vision.frame import Frame
from vision_hub.vision.sources import Pacer, SyntheticSource, SyntheticSourceConfig
from vision_hub.vision.worker import CameraWorker, EncodingSettings, encode_stream_frame

RESOLUTIONS = {"720p": (1280, 720), "1080p": (1920, 1080)}
STREAM = EncodingSettings()


def generate(width: int, height: int, count: int, fps: float = 10) -> list[Frame]:
    """A walk-through that starts after one second and lasts most of the clip."""
    config = SyntheticSourceConfig(
        width=width, height=height, fps=fps, visit_every_seconds=60, visit_seconds=59, seed=1
    )
    source = SyntheticSource(config, paced=False)
    source.open()
    try:
        return [source.read().copy() for _ in range(count)]
    finally:
        source.close()


class ReplaySource:
    """Pre-generated frames at a camera-like pace: costs (almost) nothing to produce."""

    def __init__(self, frames: Sequence[Frame], fps: float) -> None:
        self._frames = itertools.cycle(frames)
        self._pacer = Pacer(fps)
        self._fps = fps
        self._resolution = (frames[0].shape[1], frames[0].shape[0])

    @property
    def name(self) -> str:
        return "replay"

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def resolution(self) -> tuple[int, int]:
        return self._resolution

    def open(self) -> None:
        pass

    def read(self) -> Frame:
        self._pacer.wait()
        return next(self._frames)

    def close(self) -> None:
        pass


def timed(work: Callable[[], object], repeats: int) -> str:
    """Median and 95th percentile, in milliseconds."""
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        work()
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return f"{statistics.median(samples):.2f} / {samples[int(len(samples) * 0.95) - 1]:.2f}"


def annotate_then_downscale(frame: Frame, boxes: Sequence[BoundingBox]) -> bytes:
    """The Phase 2 live-frame path, kept for comparison."""
    annotated = draw_boxes(frame, boxes)
    small = cv2.resize(
        annotated, (960, round(960 * frame.shape[0] / frame.shape[1])), None, 0, 0, cv2.INTER_AREA
    )
    return cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, STREAM.stream_jpeg_quality])[
        1
    ].tobytes()


def stage_rows(width: int, height: int, repeats: int) -> dict[str, str]:
    clip = generate(width, height, 40)
    moving = clip[-1]
    jpeg = cv2.imencode(".jpg", moving, [cv2.IMWRITE_JPEG_QUALITY, 85])[1]
    rows = {
        "JPEG decode (camera-side proxy)": timed(
            lambda: cv2.imdecode(jpeg, cv2.IMREAD_COLOR), repeats
        )
    }
    for processing_width in (320, 640, 960, width):
        detector = MotionDetector(
            DetectionConfig(processing_width=processing_width, warmup_frames=0)
        )
        for frame in clip[:-1]:
            detector.process(frame)
        label = "native" if processing_width == width else f"{processing_width} px"
        rows[f"Detect @ {label}"] = timed(partial(detector.process, moving), repeats)

    detector = MotionDetector(DetectionConfig(warmup_frames=0))
    boxes: tuple[BoundingBox, ...] = ()
    for frame in clip:
        boxes = detector.process(frame).boxes
    for interpolation, name in ((cv2.INTER_AREA, "INTER_AREA"), (cv2.INTER_LINEAR, "INTER_LINEAR")):
        size = (960, round(960 * height / width))
        resize = partial(cv2.resize, moving, size, None, 0, 0, interpolation)
        rows[f"Resize to 960 px, {name}"] = timed(resize, repeats)
    rows["Live frame: annotate, downscale, encode (before)"] = timed(
        lambda: annotate_then_downscale(moving, boxes), repeats
    )
    rows["Live frame: downscale, annotate, encode (after)"] = timed(
        lambda: encode_stream_frame(moving, boxes, STREAM), repeats
    )
    rows["Live frame without motion (after)"] = timed(
        lambda: encode_stream_frame(moving, (), STREAM), repeats
    )
    return rows


def stage_report(repeats: int) -> None:
    columns = {label: stage_rows(w, h, repeats) for label, (w, h) in RESOLUTIONS.items()}
    print("## Per-frame stage cost (ms, median / p95)\n")
    print(f"| Stage | {' | '.join(columns)} |")
    print(f"|-------|{'|'.join('------' for _ in columns)}|")
    for stage in next(iter(columns.values())):
        print(f"| {stage} | {' | '.join(rows[stage] for rows in columns.values())} |")


@dataclass
class CountingSink:
    viewers: bool
    frames: int = 0

    @property
    def has_viewers(self) -> bool:
        return self.viewers

    def publish_frame(self, packet: FramePacket) -> None:
        self.frames += 1

    def publish_event(self, event: CameraEvent) -> None:
        pass

    def worker_exited(self, *, crashed: bool) -> None:
        pass


@dataclass(frozen=True)
class CpuCase:
    cameras: int
    resolution: str
    camera_fps: float
    target_fps: float
    viewers: bool


def cpu_per_camera(case: CpuCase, clip: Sequence[Frame], seconds: float) -> tuple[float, float]:
    """Process CPU over wall time per camera (100 % = one core), and analysed frames/s.

    With a live viewer every analysed frame is published, so published frames/s is the
    analysed rate."""
    sinks = [CountingSink(case.viewers) for _ in range(case.cameras)]
    workers = [
        CameraWorker(
            device_id=f"cam-{n}",
            source=ReplaySource(clip, case.camera_fps),
            detection=DetectionConfig(),
            sink=sink,
            target_fps=case.target_fps,
        )
        for n, sink in enumerate(sinks)
    ]
    for worker in workers:
        worker.start()
    time.sleep(2)  # warm up the background model
    cpu, wall, published = time.process_time(), time.perf_counter(), sum(s.frames for s in sinks)
    time.sleep(seconds)
    cpu, wall = time.process_time() - cpu, time.perf_counter() - wall
    published = sum(s.frames for s in sinks) - published
    for worker in workers:
        worker.stop()
    return cpu / wall / case.cameras * 100, published / wall / case.cameras


def cpu_report(seconds: float) -> None:
    clips = {label: generate(w, h, 50) for label, (w, h) in RESOLUTIONS.items()}
    print("\n## CPU per camera (% of one core, frame generation excluded)\n")
    print(
        "| Cameras | Resolution | Camera fps | Target fps | Live viewer "
        "| CPU / camera | Analysed fps |"
    )
    print(
        "|---------|------------|------------|------------|-------------|--------------|--------------|"
    )
    cases = (
        CpuCase(1, "720p", 10, 10, viewers=True),
        CpuCase(1, "720p", 30, 10, viewers=True),
        CpuCase(1, "1080p", 30, 10, viewers=False),
        CpuCase(1, "1080p", 30, 10, viewers=True),
        CpuCase(4, "1080p", 30, 10, viewers=True),
        CpuCase(8, "1080p", 30, 10, viewers=True),
    )
    for case in cases:
        cpu, analysed = cpu_per_camera(case, clips[case.resolution], seconds)
        viewer = "yes" if case.viewers else "no (1 frame/s)"
        print(
            f"| {case.cameras} | {case.resolution} | {case.camera_fps:g} | {case.target_fps:g} "
            f"| {viewer} | {cpu:.1f} % | {analysed:.1f} |"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline stage and per-camera CPU benchmark")
    parser.add_argument("--repeats", type=int, default=200)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--only", choices=["stages", "cpu"])
    parser.add_argument("--opencv-threads", type=int, help="cv2.setNumThreads before running")
    args = parser.parse_args()
    if args.opencv_threads is not None:
        cv2.setNumThreads(args.opencv_threads)
    print(f"OpenCV {cv2.__version__}, {cv2.getNumThreads()} OpenCV threads\n")
    if args.only != "cpu":
        stage_report(args.repeats)
    if args.only != "stages":
        cpu_report(args.seconds)


if __name__ == "__main__":
    main()
