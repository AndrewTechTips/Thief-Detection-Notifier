"""How should camera work run alongside the event loop? Saturated throughput and loop lag.

    uv run python benchmarks/concurrency.py

Each simulated camera loops as fast as it can over: decode a 1080p JPEG (stand-in for the
camera's own decoding), detect motion, encode a live-view JPEG. Strategies:

* thread:       one dedicated thread per camera (what the hub does, AD-7);
* to_thread:    one asyncio task per camera, each frame sent to ``asyncio.to_thread``;
* process:      one process per camera, live JPEGs sent back over a pipe;
* process-pool: frames decoded in the hub, each analysed in a ``ProcessPoolExecutor``.

Event-loop lag is how late a 5 ms ticker wakes up: what every HTTP request and WebSocket
message would wait on top of its own work.
"""

import argparse
import asyncio
import multiprocessing
import os
import sys
import threading
import time
from collections.abc import Callable, Coroutine, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from pipeline import generate

from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.detector import MotionDetector
from vision_hub.vision.frame import Frame
from vision_hub.vision.worker import EncodingSettings, encode_stream_frame

STREAM = EncodingSettings()
type Strategy = Callable[["Meter", asyncio.Event], Coroutine[Any, Any, None]]


def clip_jpegs(count: int = 30) -> list[bytes]:
    return [
        cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
        for frame in generate(1920, 1080, count)
    ]


class Camera:
    """One camera's work: decode, detect, encode."""

    def __init__(self, jpegs: Sequence[bytes], offset: int) -> None:
        self._jpegs = jpegs
        self._index = offset
        self._detector = MotionDetector(DetectionConfig(warmup_frames=0))

    def next_frame(self) -> Frame:
        self._index = (self._index + 1) % len(self._jpegs)
        encoded = np.frombuffer(self._jpegs[self._index], np.uint8)
        return np.asarray(cv2.imdecode(encoded, cv2.IMREAD_COLOR), np.uint8)

    def analyse(self, frame: Frame) -> bytes:
        boxes = self._detector.process(frame).boxes
        return encode_stream_frame(frame, boxes, STREAM)[0]

    def step(self) -> bytes:
        return self.analyse(self.next_frame())


@dataclass
class Result:
    frames: int
    seconds: float
    lags_ms: list[float]

    @property
    def fps(self) -> float:
        return self.frames / self.seconds

    def lag(self, quantile: float) -> float:
        ordered = sorted(self.lags_ms)
        return ordered[min(len(ordered) - 1, int(len(ordered) * quantile))]


class Meter:
    """Counts frames delivered to the loop and samples loop lag."""

    def __init__(self) -> None:
        self.frames = 0
        self.lags: list[float] = []

    def delivered(self, _jpeg: bytes) -> None:
        self.frames += 1

    async def tick(self, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            expected = loop.time() + 0.005
            await asyncio.sleep(0.005)
            self.lags.append(max(0.0, loop.time() - expected) * 1000)


async def measure(run: Strategy, seconds: float) -> Result:
    meter, stop = Meter(), asyncio.Event()
    ticker = asyncio.create_task(meter.tick(stop))
    worker = asyncio.create_task(run(meter, stop))
    await asyncio.sleep(1)  # warm up
    frames, wall = meter.frames, time.perf_counter()
    meter.lags.clear()
    await asyncio.sleep(seconds)
    result = Result(meter.frames - frames, time.perf_counter() - wall, list(meter.lags))
    stop.set()
    await asyncio.gather(ticker, worker)
    return result


# ── Strategies ───────────────────────────────────────────────────────


def threads(cameras: int, jpegs: Sequence[bytes]) -> Strategy:
    async def run(meter: Meter, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        done = threading.Event()

        def camera(offset: int) -> None:
            cam = Camera(jpegs, offset)
            while not done.is_set():
                loop.call_soon_threadsafe(meter.delivered, cam.step())

        workers = [threading.Thread(target=camera, args=(n,)) for n in range(cameras)]
        for worker in workers:
            worker.start()
        await stop.wait()
        done.set()
        for worker in workers:
            await asyncio.to_thread(worker.join)

    return run


def to_thread(cameras: int, jpegs: Sequence[bytes]) -> Strategy:
    async def run(meter: Meter, stop: asyncio.Event) -> None:
        async def camera(offset: int) -> None:
            cam = Camera(jpegs, offset)
            while not stop.is_set():
                meter.delivered(await asyncio.to_thread(cam.step))

        await asyncio.gather(*(camera(n) for n in range(cameras)))

    return run


def _camera_process(jpegs: Sequence[bytes], offset: int, conn: Connection, threads: int) -> None:
    cv2.setNumThreads(threads)
    cam = Camera(jpegs, offset)
    try:
        while True:
            conn.send_bytes(cam.step())
    except BrokenPipeError, EOFError, OSError:
        pass


def processes(cameras: int, jpegs: Sequence[bytes], opencv_threads: int) -> Strategy:
    async def run(meter: Meter, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        context = multiprocessing.get_context("spawn")
        pipes = [context.Pipe(duplex=False) for _ in range(cameras)]
        children = [
            context.Process(target=_camera_process, args=(jpegs, n, send, opencv_threads))
            for n, (_, send) in enumerate(pipes)
        ]
        for child in children:
            child.start()
        for receive, _ in pipes:
            loop.add_reader(receive.fileno(), _forward, receive, meter)
        await stop.wait()
        for receive, send in pipes:
            loop.remove_reader(receive.fileno())
            receive.close()
            send.close()
        for child in children:
            child.terminate()
            child.join()

    return run


def _forward(receive: Connection, meter: Meter) -> None:
    meter.delivered(receive.recv_bytes())


_POOL_CAMERA: dict[int, Camera] = {}


def _pool_init(threads: int) -> None:
    cv2.setNumThreads(threads)


def _pool_analyse(offset: int, frame: Frame) -> bytes:
    camera = _POOL_CAMERA.setdefault(offset, Camera([b""], offset))
    return camera.analyse(frame)


def process_pool(cameras: int, jpegs: Sequence[bytes], opencv_threads: int) -> Strategy:
    async def run(meter: Meter, stop: asyncio.Event) -> None:
        loop = asyncio.get_running_loop()
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(
            max_workers=min(cameras, os.cpu_count() or 1),
            mp_context=context,
            initializer=_pool_init,
            initargs=(opencv_threads,),
        ) as pool:

            async def camera(offset: int) -> None:
                decoder = Camera(jpegs, offset)
                while not stop.is_set():
                    frame = await asyncio.to_thread(decoder.next_frame)  # capture in the hub
                    meter.delivered(await loop.run_in_executor(pool, _pool_analyse, offset, frame))

            await asyncio.gather(*(camera(n) for n in range(cameras)))

    return run


async def report(cameras_list: Sequence[int], seconds: float, opencv_threads: int) -> None:
    cv2.setNumThreads(opencv_threads)
    jpegs = clip_jpegs()
    print(f"CPU cores: {os.cpu_count()}, OpenCV threads per process: {opencv_threads}\n")
    print("| Strategy | Cameras | Frames/s (total) | Loop lag p50 / p99 / max (ms) |")
    print("|----------|---------|------------------|-------------------------------|")
    strategies: dict[str, Callable[[int], Strategy]] = {
        "thread": lambda n: threads(n, jpegs),
        "to_thread": lambda n: to_thread(n, jpegs),
        "process": lambda n: processes(n, jpegs, opencv_threads),
        "process-pool": lambda n: process_pool(n, jpegs, opencv_threads),
    }
    for name, strategy in strategies.items():
        for cameras in cameras_list:
            result = await measure(strategy(cameras), seconds)
            lag = f"{result.lag(0.5):.1f} / {result.lag(0.99):.1f} / {max(result.lags_ms):.1f}"
            print(f"| {name} | {cameras} | {result.fps:.0f} | {lag} |")


def main() -> None:
    parser = argparse.ArgumentParser(description="Camera concurrency strategies")
    parser.add_argument("--cameras", type=int, nargs="+", default=[1, 4, 8, 16])
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--opencv-threads", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(report(args.cameras, args.seconds, args.opencv_threads))


if __name__ == "__main__":
    main()
