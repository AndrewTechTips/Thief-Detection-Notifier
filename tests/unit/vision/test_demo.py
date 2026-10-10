import hashlib
from pathlib import Path

import cv2
import numpy as np
import pytest

from vision_hub.vision.demo import (
    CLIPS,
    FPS,
    DemoClip,
    back_and_forth,
    crossfade,
    make_loop,
    prepare_footage,
)
from vision_hub.vision.frame import Frame


def flat(value: int) -> Frame:
    return np.full((4, 4, 3), value, np.uint8)


def write_source(
    path: Path, *, seconds: float, fps: float = 10, size: tuple[int, int] = (700, 395)
) -> None:
    """A square crossing a grey room: odd sizes, so the loop has to fix them."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"VP80"), fps, size)
    frames = round(seconds * fps)
    for index in range(frames):
        frame = np.full((size[1], size[0], 3), 90, np.uint8)
        x = 20 + index * (size[0] - 140) // frames
        cv2.rectangle(frame, (x, 150), (x + 100, 300), (230, 230, 230), -1)
        writer.write(frame)
    writer.release()


def read_all(path: Path) -> list[Frame]:
    capture = cv2.VideoCapture(str(path))
    frames = []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(np.asarray(frame, np.uint8))
    capture.release()
    return frames


def clip(path: Path, **overrides: object) -> DemoClip:
    fields: dict[str, object] = {
        "name": "room",
        "title": "Room",
        "author": "Tests",
        "page": "https://example.com/room",
        "url": path.as_uri(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "0" * 64,
        "end": 2.0,
        "idle": 0.5,
        "idle_seconds": 2.0,
    }
    return DemoClip(**(fields | overrides))  # type: ignore[arg-type]


class TestPieces:
    def test_crossfade_blends_into_the_end_frame(self) -> None:
        means = [int(frame.mean()) for frame in crossfade(flat(0), flat(90), 3)]

        assert means == [30, 60, 90]

    def test_back_and_forth_ends_where_the_loop_starts(self) -> None:
        frames = [flat(value) for value in (0, 1, 2)]

        played = [int(frame[0, 0, 0]) for frame in back_and_forth(frames, 8)]

        assert played == [1, 2, 1, 0, 1, 2, 1, 0]

    def test_back_and_forth_needs_something_to_play(self) -> None:
        assert list(back_and_forth([flat(0), flat(1)], 0)) == []
        assert list(back_and_forth([flat(0)], 10)) == []


class TestLoop:
    def test_a_loop_is_the_clip_a_crossfade_and_the_idle_scene(self, tmp_path: Path) -> None:
        source = tmp_path / "source.webm"
        write_source(source, seconds=3)
        output = tmp_path / "loops" / "room.webm"

        make_loop(source, clip(source), output)

        frames = read_all(output)
        assert frames[0].shape == (360, 640, 3)  # 640 wide, odd height trimmed to even
        # 2 s of clip at 15 fps, a 1.5 s crossfade, then about 2 s of idle scene.
        assert 31 + 22 + 28 <= len(frames) <= 31 + 23 + 32
        # The loop closes: its last frame is its first one again.
        difference = np.abs(frames[-1].astype(int) - frames[0].astype(int)).mean()
        assert difference < 3
        assert not list(tmp_path.glob("loops/.*"))  # no temporary file left

    def test_without_an_idle_scene_the_loop_ends_on_the_crossfade(self, tmp_path: Path) -> None:
        source = tmp_path / "source.webm"
        write_source(source, seconds=3)
        output = tmp_path / "room.webm"

        make_loop(source, clip(source, idle=0.0, idle_seconds=0.0), output)

        assert len(read_all(output)) == round(2 * FPS) + 1 + 22

    def test_wider_than_16_9_is_cropped_around_the_centre(self, tmp_path: Path) -> None:
        source = tmp_path / "source.webm"
        write_source(source, seconds=1, size=(1000, 360))
        output = tmp_path / "room.webm"

        make_loop(source, clip(source, end=0.5), output)

        assert read_all(output)[0].shape == (360, 640, 3)

    def test_an_unreadable_source_fails(self, tmp_path: Path) -> None:
        source = tmp_path / "source.mp4"
        source.write_bytes(b"not a video")

        with pytest.raises(ValueError, match="no readable frames"):
            make_loop(source, clip(source), tmp_path / "room.webm")


class TestPrepare:
    def test_fetches_verified_sources_and_makes_missing_loops_once(self, tmp_path: Path) -> None:
        remote = tmp_path / "remote.webm"
        write_source(remote, seconds=2)
        directory = tmp_path / "demo"

        first = prepare_footage(directory, [clip(remote)])
        second = prepare_footage(directory, [clip(remote)])

        assert [made for _, made in first] == [True]
        assert [made for _, made in second] == [False]
        assert (directory / "sources" / "room.mp4").read_bytes() == remote.read_bytes()
        assert (directory / "room.webm").is_file()

    def test_without_fetching_uses_the_sources_already_there(self, tmp_path: Path) -> None:
        directory = tmp_path / "demo"
        (directory / "sources").mkdir(parents=True)
        write_source(tmp_path / "room.webm", seconds=2)  # OpenCV can't put VP8 in an .mp4
        (tmp_path / "room.webm").rename(directory / "sources" / "room.mp4")

        made = prepare_footage(directory, [clip(tmp_path / "nowhere.mp4")], fetch=False)

        assert [made for _, made in made] == [True]


def test_every_clip_is_pinned_and_credited() -> None:
    assert len({c.name for c in CLIPS}) == len(CLIPS)
    for demo in CLIPS:
        assert demo.url.startswith("https://videos.pexels.com/")
        assert demo.page.startswith("https://www.pexels.com/video/")
        assert len(demo.sha256) == 64
        assert demo.author
        assert 0 <= demo.idle < demo.end


def test_the_credits_list_every_clip() -> None:
    page = (Path(__file__).parents[3] / "docs" / "demo-footage.md").read_text()

    for demo in CLIPS:
        assert demo.page in page
        assert demo.author in page
