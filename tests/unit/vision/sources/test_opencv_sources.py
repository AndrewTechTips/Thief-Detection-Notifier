import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from pydantic import SecretStr

from vision_hub.vision.frame import Frame
from vision_hub.vision.sources import (
    RtspSourceConfig,
    SourceError,
    VideoFileSourceConfig,
    WebcamSourceConfig,
)
from vision_hub.vision.sources.opencv import (
    FFMPEG_CAPTURE_OPTIONS,
    RtspSource,
    VideoFileSource,
    WebcamSource,
)


def solid(value: int, width: int = 64, height: int = 48) -> Frame:
    return np.full((height, width, 3), value, dtype=np.uint8)


class FakeCapture:
    """Stands in for ``cv2.VideoCapture``: scripted frames, recorded calls."""

    def __init__(
        self,
        frames: list[Frame | None] | None = None,
        *,
        opened: bool = True,
        props: dict[int, float] | None = None,
    ) -> None:
        self.frames = list(frames or [])
        self.opened = opened
        self.props = props or {}
        self.set_calls: list[tuple[int, float]] = []
        self.released = False

    def isOpened(self) -> bool:  # noqa: N802 - OpenCV's name
        return self.opened

    def read(self) -> tuple[bool, Any]:
        if not self.frames:
            return False, None
        frame = self.frames.pop(0)
        return frame is not None, frame

    def get(self, prop_id: int) -> float:
        return self.props.get(prop_id, 0.0)

    def set(self, prop_id: int, value: float) -> bool:
        self.set_calls.append((prop_id, value))
        return True

    def release(self) -> None:
        self.released = True


class Factory:
    def __init__(self, capture: FakeCapture) -> None:
        self.capture = capture
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any) -> FakeCapture:
        self.calls.append(args)
        return self.capture


class TestWebcamSource:
    def test_opens_the_device_and_requests_the_mode(self) -> None:
        factory = Factory(FakeCapture())
        source = WebcamSource(
            WebcamSourceConfig(index=2, width=1280, height=720, fps=15), capture_factory=factory
        )

        source.open()

        assert factory.calls == [(2,)]
        assert factory.capture.set_calls == [
            (cv2.CAP_PROP_FRAME_WIDTH, 1280),
            (cv2.CAP_PROP_FRAME_HEIGHT, 720),
            (cv2.CAP_PROP_FPS, 15),
        ]
        assert source.name == "webcam 2"

    def test_failed_open_raises_and_releases(self) -> None:
        capture = FakeCapture(opened=False)
        source = WebcamSource(WebcamSourceConfig(), capture_factory=Factory(capture))

        with pytest.raises(SourceError, match="cannot open webcam 0"):
            source.open()
        assert capture.released is True

    def test_reads_frames_until_the_device_fails(self) -> None:
        source = WebcamSource(
            WebcamSourceConfig(), capture_factory=Factory(FakeCapture([solid(1), None]))
        )
        source.open()

        assert int(source.read()[0, 0, 0]) == 1
        with pytest.raises(SourceError, match="stopped delivering"):
            source.read()

    def test_reading_before_open_fails(self) -> None:
        with pytest.raises(SourceError, match="not open"):
            WebcamSource(WebcamSourceConfig()).read()

    def test_reports_fps_and_resolution_only_while_open(self) -> None:
        props = {
            cv2.CAP_PROP_FPS: 30.0,
            cv2.CAP_PROP_FRAME_WIDTH: 640.0,
            cv2.CAP_PROP_FRAME_HEIGHT: 480.0,
        }
        capture = FakeCapture(props=props)
        source = WebcamSource(WebcamSourceConfig(), capture_factory=Factory(capture))
        assert (source.fps, source.resolution) == (None, None)

        source.open()
        assert (source.fps, source.resolution) == (30.0, (640, 480))

        source.close()
        source.close()  # idempotent
        assert capture.released is True
        assert source.resolution is None

    def test_unknown_fps_and_resolution_are_none(self) -> None:
        source = WebcamSource(WebcamSourceConfig(), capture_factory=Factory(FakeCapture()))
        source.open()

        assert (source.fps, source.resolution) == (None, None)


class TestRtspSource:
    @pytest.fixture(autouse=True)
    def clean_ffmpeg_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENCV_FFMPEG_LOGLEVEL", raising=False)
        monkeypatch.delenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", raising=False)

    def test_opens_with_credentials_ffmpeg_and_timeouts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        factory = Factory(FakeCapture())
        config = RtspSourceConfig(
            url="rtsp://cam.local/stream",
            username="admin",
            password=SecretStr("hunter2"),
            open_timeout_seconds=3,
            read_timeout_seconds=4,
        )

        RtspSource(config, capture_factory=factory).open()

        [(url, backend, params)] = factory.calls
        assert url == "rtsp://admin:hunter2@cam.local/stream"
        assert backend == cv2.CAP_FFMPEG
        assert list(params) == [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
            3000,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC,
            4000,
        ]

    def test_silences_ffmpeg_logs_and_prefers_tcp(self) -> None:
        import os

        RtspSource(
            RtspSourceConfig(url="rtsp://cam.local/s"), capture_factory=Factory(FakeCapture())
        ).open()

        assert os.environ["OPENCV_FFMPEG_LOGLEVEL"] == "-8"
        assert os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] == FFMPEG_CAPTURE_OPTIONS
        assert "rtsp_transport;tcp" in FFMPEG_CAPTURE_OPTIONS

    def test_operator_ffmpeg_options_are_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import os

        monkeypatch.setenv("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;udp")

        RtspSource(
            RtspSourceConfig(url="rtsp://cam.local/s"), capture_factory=Factory(FakeCapture())
        ).open()

        assert os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] == "rtsp_transport;udp"

    def test_open_that_used_up_its_timeout_is_a_failure(self) -> None:
        """Regression test (found against a live RTSP server): when FFmpeg's open timeout
        fires, OpenCV can report success, but every read after the buffered frames fails."""
        now = [0.0]
        capture = FakeCapture()

        def slow_factory(*_args: Any) -> FakeCapture:
            now[0] += 3.0  # the open took the whole 3 s timeout
            return capture

        source = RtspSource(
            RtspSourceConfig(url="rtsp://cam.local/s", open_timeout_seconds=3),
            capture_factory=slow_factory,
            clock=lambda: now[0],
        )

        with pytest.raises(SourceError, match="increase open_timeout_seconds"):
            source.open()
        assert capture.released is True

    def test_name_and_errors_never_contain_the_password(self) -> None:
        config = RtspSourceConfig(
            url="rtsp://cam.local/s", username="admin", password=SecretStr("hunter2")
        )
        source = RtspSource(config, capture_factory=Factory(FakeCapture(opened=False)))

        with pytest.raises(SourceError) as exc_info:
            source.open()

        assert source.name == "stream rtsp://cam.local/s"
        assert "hunter2" not in str(exc_info.value)


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    """A real 5-frame, 10 fps MJPEG file whose frames are 0, 50, 100, 150, 200 grey."""
    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"MJPG"), 10, (64, 48))
    for value in range(0, 250, 50):
        writer.write(solid(value))
    writer.release()
    return path


class TestVideoFileSource:
    def test_reads_a_real_file(self, video_file: Path) -> None:
        source = VideoFileSource(VideoFileSourceConfig(path=video_file, loop=False), paced=False)
        source.open()

        values = [int(source.read()[24, 32, 0]) for _ in range(5)]

        assert values == pytest.approx([0, 50, 100, 150, 200], abs=3)  # JPEG is lossy
        assert source.resolution == (64, 48)
        assert source.fps == 10
        assert source.name == "file clip.avi"

    def test_loops_back_to_the_start(self, video_file: Path) -> None:
        source = VideoFileSource(VideoFileSourceConfig(path=video_file), paced=False)
        source.open()

        values = [int(source.read()[24, 32, 0]) for _ in range(7)]

        assert values[5:] == pytest.approx([0, 50], abs=3)

    def test_without_loop_the_end_is_an_error(self, video_file: Path) -> None:
        source = VideoFileSource(VideoFileSourceConfig(path=video_file, loop=False), paced=False)
        source.open()
        for _ in range(5):
            source.read()

        with pytest.raises(SourceError, match="no more frames"):
            source.read()

    def test_missing_file_fails_to_open(self, tmp_path: Path) -> None:
        source = VideoFileSource(VideoFileSourceConfig(path=tmp_path / "nope.mp4"))

        with pytest.raises(SourceError, match="does not exist"):
            source.open()

    def test_paced_playback_runs_at_the_file_frame_rate(self, video_file: Path) -> None:
        source = VideoFileSource(VideoFileSourceConfig(path=video_file))
        source.open()

        started = time.perf_counter()
        for _ in range(4):
            source.read()

        assert time.perf_counter() - started >= 0.28  # 3 intervals of 0.1 s at 10 fps

    def test_falls_back_to_a_default_rate_when_unknown(self, video_file: Path) -> None:
        source = VideoFileSource(
            VideoFileSourceConfig(path=video_file),
            capture_factory=Factory(FakeCapture([solid(1)])),
        )
        source.open()

        assert source.fps is None
        assert int(source.read()[0, 0, 0]) == 1
