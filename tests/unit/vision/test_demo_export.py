import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from vision_hub.domain.motion import BoundingBox
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.demo_export import export_demo
from vision_hub.vision.fleet import DeviceSpec
from vision_hub.vision.frame import Frame
from vision_hub.vision.persons import Person
from vision_hub.vision.sources import SyntheticSourceConfig, VideoFileSourceConfig

FPS = 15


def write_loop(path: Path, *, seconds: float = 6, visit: tuple[float, float] = (2, 4)) -> None:
    """A grey room a square crosses between ``visit`` seconds, like a demo loop."""
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"VP80"), FPS, (320, 180))
    for index in range(round(seconds * FPS)):
        frame = np.full((180, 320, 3), 90, np.uint8)
        at = index / FPS
        if visit[0] <= at < visit[1]:
            x = 20 + int((at - visit[0]) / (visit[1] - visit[0]) * 240)
            cv2.rectangle(frame, (x, 50), (x + 50, 150), (230, 230, 230), -1)
        writer.write(frame)
    writer.release()


class Everyone:
    """Sees a person on every frame it is shown."""

    def detect(self, frame: Frame) -> list[Person]:
        return [Person(box=BoundingBox(x=0, y=0, width=10, height=20), confidence=0.9)]


def camera(path: Path, **detection: object) -> DeviceSpec:
    return DeviceSpec(
        id="room",
        name="Room",
        source=VideoFileSourceConfig(path=path),
        detection=DetectionConfig(motion_end_grace_seconds=0.5, **detection),  # type: ignore[arg-type]
    )


def test_records_frames_and_events_of_one_pass(tmp_path: Path) -> None:
    loop = tmp_path / "room.webm"
    write_loop(loop)
    output = tmp_path / "demo"

    [summary] = export_demo([camera(loop, alert_on="person")], Everyone(), output)

    manifest = json.loads((output / "manifest.json").read_text())
    [room] = manifest["cameras"]
    assert manifest["version"] == 1
    assert (room["id"], room["name"], room["width"], room["height"]) == ("room", "Room", 320, 180)
    assert room["duration"] == pytest.approx(6, abs=0.1)
    assert room["source"]["kind"] == "video_file"
    assert room["detection"]["alert_on"] == "person"
    # One pass at the camera's 10 fps, in loop time, boxes only while the square moves.
    times = [t for t, _ in room["frames"]]
    assert times == sorted(times)
    assert times[0] >= 0
    assert times[-1] < 6
    assert 55 <= len(times) <= 61
    moving = [t for t, found in room["frames"] if found and found["boxes"]]
    assert moving
    assert min(moving) >= 1.9
    assert max(moving) <= 4.1
    # The visit, with its verdict and the files the hub would store.
    [event] = room["events"]
    assert 1.9 <= event["start"] <= 2.6
    assert event["start"] < event["end"] <= 5.5
    assert (event["person"], event["person_confidence"], event["alert"]) == (True, 0.9, True)
    for kind in ("clean", "annotated", "thumbnail"):
        assert (output / event["snapshots"][kind]["path"]).read_bytes()[:2] == b"\xff\xd8"
    assert (output / event["clip"]["path"]).stat().st_size == event["clip"]["size_bytes"]
    assert (output / room["loop"]).read_bytes() == loop.read_bytes()
    assert (summary.camera, summary.events, summary.people) == ("room", 1, 1)


def test_only_video_file_cameras_can_be_exported(tmp_path: Path) -> None:
    synthetic = DeviceSpec(id="sim", name="Sim", source=SyntheticSourceConfig())

    with pytest.raises(ValueError, match="video file"):
        export_demo([synthetic], Everyone(), tmp_path)


def test_a_missing_loop_says_how_to_make_it(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="demo-footage"):
        export_demo([camera(tmp_path / "missing.webm")], Everyone(), tmp_path / "out")
