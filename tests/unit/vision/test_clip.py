"""Event clips: real VP8 encoding, decoded again to check what a viewer would see."""

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest

from vision_hub.domain.events import VideoClip
from vision_hub.vision.clip import ClipError, ClipRecorder, ClipSettings
from vision_hub.vision.frame import Frame

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
FPS = 10


def frame(level: int, *, size: tuple[int, int] = (480, 640), gray: bool = False) -> Frame:
    """A frame whose brightness says which one it is (decoded video is lossy, levels aren't)."""
    shape = size if gray else (*size, 3)
    return np.full(shape, level, np.uint8)


def at(index: int) -> datetime:
    return T0 + timedelta(seconds=index / FPS)


def decode(clip: VideoClip) -> list[Frame]:
    """Frames as a browser would show them."""
    with tempfile.NamedTemporaryFile(suffix=".webm") as file:
        file.write(clip.data)
        file.flush()
        capture = cv2.VideoCapture(file.name)
        frames = []
        while True:
            ok, image = capture.read()
            if not ok:
                break
            frames.append(np.asarray(image, np.uint8))
        capture.release()
    return frames


def levels(clip: VideoClip) -> list[int]:
    return [round(int(image.mean()) / 10) * 10 for image in decode(clip)]


def recorder(**settings: float) -> ClipRecorder:
    values: dict[str, float] = {"pre_roll_seconds": 0.5, "max_seconds": 60, "width": 320}
    return ClipRecorder(ClipSettings(**(values | settings)), fps=FPS)  # type: ignore[arg-type]


def test_a_clip_has_the_seconds_before_the_motion_then_the_event() -> None:
    clips = recorder()
    for i in range(20):  # idle: only the last 0.5 s (5 frames) are kept
        clips.add(frame(20), at(i))
    clips.add(frame(60), at(20))  # the frame just before detection

    clips.start()
    for i in range(21, 26):
        clips.add(frame(200), at(i))
    clip = clips.finish()

    assert clip is not None
    assert levels(clip) == [20] * 4 + [60] + [200] * 5
    assert (clip.content_type, clip.width, clip.height) == ("video/webm", 320, 240)
    assert clip.duration_seconds == pytest.approx(1.0)
    assert clip.data[:4] == b"\x1a\x45\xdf\xa3"  # EBML: a WebM file


def test_frames_keep_their_timing_when_the_camera_falls_behind() -> None:
    clips = recorder(pre_roll_seconds=0)
    clips.start()

    clips.add(frame(40), at(0))
    clips.add(frame(120), at(5))  # half a second later: shown for half a second
    clips.add(frame(200), at(5) + timedelta(seconds=30))  # a reconnect: no 30 s freeze
    clip = clips.finish()

    assert clip is not None
    assert levels(clip) == [40] + [120] * 5 + [200]


def test_long_events_keep_their_start() -> None:
    clips = recorder(pre_roll_seconds=0, max_seconds=1)
    clips.start()

    for i in range(40):
        clips.add(frame(100), at(i))
    clip = clips.finish()

    assert clip is not None
    assert len(decode(clip)) == 11  # 0 s to 1 s inclusive


def test_grey_frames_and_a_resolution_change_mid_event_keep_one_size() -> None:
    clips = recorder(pre_roll_seconds=0, width=640)
    clips.start()

    clips.add(frame(50, size=(361, 641), gray=True), at(0))  # odd sizes: VP8 needs even
    clips.add(frame(150, size=(1080, 1920)), at(1))
    clip = clips.finish()

    assert clip is not None
    assert (clip.width, clip.height) == (640, 360)
    assert {image.shape for image in decode(clip)} == {(360, 640, 3)}


def test_nothing_to_finish() -> None:
    clips = recorder()
    assert clips.finish() is None  # never started

    clips.start()
    assert clips.finish() is None  # started without frames (and no pre-roll yet)


@pytest.fixture
def temporary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Where clips are written while recording."""
    directory = tmp_path / "tmp"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    return directory


def test_temporary_files_are_removed(temporary: Path) -> None:
    clips = recorder(pre_roll_seconds=0)

    clips.start()
    clips.add(frame(10), at(0))
    assert len(list(temporary.iterdir())) == 1
    assert clips.finish() is not None
    clips.start()
    clips.add(frame(10), at(0))
    clips.start()  # a new event drops the unfinished clip
    clips.abort()

    assert list(temporary.iterdir()) == []
    assert not clips.recording


class ClosedWriter:
    def __init__(self, *_: object) -> None: ...

    def isOpened(self) -> bool:  # noqa: N802 - OpenCV's name
        return False

    def release(self) -> None: ...


def test_a_missing_encoder_is_an_error_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, temporary: Path
) -> None:
    monkeypatch.setattr(cv2, "VideoWriter", ClosedWriter)
    clips = recorder(pre_roll_seconds=0)
    clips.start()

    with pytest.raises(ClipError, match="VP8"):
        clips.add(frame(10), at(0))
    clips.abort()

    assert list(temporary.iterdir()) == []
