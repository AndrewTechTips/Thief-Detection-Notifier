"""Person detection: decoding YOLOX output, alert decisions, loading and fetching the model, and
(when the model has been downloaded) the real thing on a photo of a person."""

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
import pytest

from vision_hub.domain.motion import BoundingBox
from vision_hub.vision.persons import (
    INPUT_SIZE,
    ModelError,
    YoloxPersonDetector,
    _grids,
    decode,
    download_model,
    letterbox,
    load_person_detector,
    should_alert,
)

ASSETS = Path(__file__).resolve().parents[2] / "assets"
MODEL = Path(__file__).resolve().parents[3] / "data" / "models" / "person-yolox-s.onnx"
GRIDS, STRIDES = _grids(INPUT_SIZE)
type LogRecords = Callable[[], list[dict[str, Any]]]


def raw_output() -> npt.NDArray[np.float32]:
    """YOLOX output with nothing in it: every row scores zero."""
    output = np.zeros((len(GRIDS), 85), np.float32)
    output[:, 2:4] = np.log(1.0)  # 1 cell wide
    return output


def put(
    output: npt.NDArray[np.float32],
    *,
    stride: int,
    cell: tuple[int, int],
    size: tuple[float, float],
    score: float,
    cls: int = 0,
) -> None:
    """One prediction: a box centred on ``cell`` of the ``stride`` grid, ``size`` in pixels."""
    same_cell = np.all(np.equal(GRIDS, np.array(cell, np.float32)), axis=1)
    row = int(np.flatnonzero((STRIDES[:, 0] == stride) & same_cell)[0])
    output[row, 0:2] = 0.0  # centre of the cell's top-left corner
    output[row, 2:4] = np.log(np.array(size) / stride)
    output[row, 4] = 1.0
    output[row, 5 + cls] = score


class TestDecoding:
    def test_a_person_comes_back_in_frame_coordinates(self) -> None:
        output = raw_output()
        put(output, stride=32, cell=(10, 8), size=(64, 128), score=0.9)

        [person] = decode(output, GRIDS, STRIDES, scale=0.5, width=1280, height=720)

        # 640 px input from a 1280 px frame: everything doubles.
        assert person.box == BoundingBox(x=576, y=384, width=128, height=256)
        assert person.confidence == pytest.approx(0.9)

    def test_weak_and_non_person_predictions_are_ignored(self) -> None:
        output = raw_output()
        put(output, stride=32, cell=(3, 3), size=(64, 64), score=0.2)  # below the floor
        put(output, stride=32, cell=(9, 9), size=(64, 64), score=0.95, cls=16)  # a dog

        assert decode(output, GRIDS, STRIDES, scale=1.0, width=640, height=640) == []

    def test_overlapping_boxes_of_one_person_count_once_and_boxes_stay_inside(self) -> None:
        output = raw_output()
        put(output, stride=16, cell=(20, 20), size=(100, 200), score=0.8)
        put(output, stride=16, cell=(20, 21), size=(100, 200), score=0.6)  # same person
        put(output, stride=32, cell=(0, 0), size=(100, 100), score=0.7)  # corner, half outside

        people = decode(output, GRIDS, STRIDES, scale=1.0, width=640, height=640)

        assert [p.confidence for p in people] == pytest.approx([0.8, 0.7])
        corner = people[1].box
        assert (corner.x, corner.y) == (0, 0)
        assert corner.width == corner.height == 50

    def test_letterbox_keeps_the_aspect_ratio(self) -> None:
        frame = np.full((480, 1280, 3), 200, np.uint8)

        boxed, scale = letterbox(frame, 640)

        assert boxed.shape == (640, 640, 3)
        assert scale == 0.5
        assert boxed[:240].min() == 200  # the picture, at the top
        assert boxed[241:].max() == 114  # grey padding below


@pytest.mark.parametrize(
    ("alert_on", "confidence", "expected"),
    [
        ("motion", None, True),
        ("motion", 0.1, True),
        ("person", 0.8, True),
        ("person", 0.5, True),
        ("person", 0.49, False),
        ("person", 0.0, False),
        ("person", None, True),  # nobody could check: alert anyway
    ],
)
def test_alert_decision(alert_on: str, confidence: float | None, expected: bool) -> None:
    assert should_alert(alert_on, confidence, 0.5) is expected  # type: ignore[arg-type]


class TestModelFile:
    def test_a_missing_model_means_no_detector(
        self, tmp_path: Path, log_records: LogRecords
    ) -> None:
        assert load_person_detector(tmp_path / "missing.onnx") is None
        assert any(r["event"] == "person_model_missing" for r in log_records())

    def test_a_broken_model_means_no_detector(
        self, tmp_path: Path, log_records: LogRecords
    ) -> None:
        broken = tmp_path / "broken.onnx"
        broken.write_bytes(b"not a model")

        assert load_person_detector(broken) is None
        with pytest.raises(ModelError):
            YoloxPersonDetector(broken)
        assert any(r["event"] == "person_model_unusable" for r in log_records())

    def test_download_checks_the_hash_and_never_leaves_a_bad_file(self, tmp_path: Path) -> None:
        source = tmp_path / "remote.onnx"
        source.write_bytes(b"model bytes")
        good = hashlib.sha256(b"model bytes").hexdigest()
        target = tmp_path / "models" / "person.onnx"

        with pytest.raises(ModelError, match="SHA-256"):
            download_model(target, url=source.as_uri(), sha256="0" * 64)
        assert list((tmp_path / "models").iterdir()) == []

        assert download_model(target, url=source.as_uri(), sha256=good) is True
        assert target.read_bytes() == b"model bytes"
        assert download_model(target, url=source.as_uri(), sha256=good) is False  # already there


@pytest.fixture(scope="module")
def detector() -> YoloxPersonDetector:
    return YoloxPersonDetector(MODEL)


@pytest.mark.skipif(not MODEL.is_file(), reason="run `vision-hub download-model` first")
class TestRealModel:
    def test_finds_the_person_in_a_photo(self, detector: YoloxPersonDetector) -> None:
        photo = cv2.imread(str(ASSETS / "person.jpg"))
        assert photo is not None

        [person, *_] = detector.detect(np.asarray(photo, np.uint8))

        assert person.confidence > 0.8
        assert person.box.width > photo.shape[1] * 0.7  # a portrait: she fills the frame

    def test_finds_nobody_in_an_empty_scene(self, detector: YoloxPersonDetector) -> None:
        rng = np.random.default_rng(3)
        scene = cv2.GaussianBlur(rng.integers(0, 255, (480, 640, 3), dtype=np.uint8), (0, 0), 9)

        assert detector.detect(np.asarray(scene, np.uint8)) == []

    def test_grey_frames_work_too(self, detector: YoloxPersonDetector) -> None:
        photo = cv2.imread(str(ASSETS / "person.jpg"), cv2.IMREAD_GRAYSCALE)

        assert detector.detect(np.asarray(photo, np.uint8))[0].confidence > 0.5
