from pathlib import Path

import pytest

from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.demo import CLIPS
from vision_hub.vision.fleet import FleetError, load_fleet
from vision_hub.vision.sources import RtspSourceConfig, SyntheticSourceConfig

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULTS = DetectionConfig(min_motion_area=0.02, pixel_threshold=50)


def device(extra: str = "", *, kind: str = "synthetic", device_id: str = "a") -> str:
    """A minimal one-device TOML document; ``extra`` lines go before the source table."""
    header = f'[[devices]]\nid = "{device_id}"\nname = "x"\n{extra}'
    return f'{header}[devices.source]\nkind = "{kind}"\n'


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "devices.toml"
    path.write_text(content)
    return path


def test_loads_devices_with_per_device_overrides(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        """
        [[devices]]
        id = "porch"
        name = "Porch"
        target_fps = 5
        [devices.source]
        kind = "synthetic"
        [devices.detection]
        min_motion_area = 0.005

        [[devices]]
        id = "gate"
        name = "Gate"
        enabled = false
        [devices.source]
        kind = "rtsp"
        url = "rtsp://10.0.0.5/live"
        """,
    )

    porch, gate = load_fleet(path, defaults=DEFAULTS)

    assert isinstance(porch.source, SyntheticSourceConfig)
    assert porch.target_fps == 5
    assert porch.detection.min_motion_area == 0.005  # overridden
    assert porch.detection.pixel_threshold == 50  # inherited from the defaults
    assert isinstance(gate.source, RtspSourceConfig)
    assert gate.enabled is False
    assert gate.detection == DEFAULTS


def test_empty_file_means_no_cameras(tmp_path: Path) -> None:
    assert load_fleet(write(tmp_path, ""), defaults=DEFAULTS) == []


def test_example_file_is_valid() -> None:
    fleet = load_fleet(PROJECT_ROOT / "devices.example.toml", defaults=DEFAULTS)

    assert [d.id for d in fleet] == ["demo-porch", "demo-garage", "driveway", "desk"]
    assert [d.enabled for d in fleet] == [True, True, False, False]


def test_demo_file_plays_every_demo_clip() -> None:
    fleet = load_fleet(PROJECT_ROOT / "devices.demo.toml", defaults=DEFAULTS)

    assert [d.id for d in fleet] == [clip.name for clip in CLIPS]
    for camera in fleet:
        assert camera.source.kind == "video_file"
        assert str(camera.source.path) == f"data/demo/{camera.id}.webm"
        assert camera.detection.alert_on == "person"


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("[[devices]\n", "not valid TOML"),
        (device(device_id="Bad Id!"), "id"),
        (device(kind="pigeon"), "invalid"),
        (device("detection = 3\n"), "table"),
        (device() + "[devices.detection]\nblur_kernel_size = 4\n", "odd"),
        ("[extra]\nx = 1", "invalid"),
    ],
    ids=[
        "bad-toml",
        "bad-id",
        "unknown-kind",
        "detection-not-table",
        "bad-override",
        "unknown-key",
    ],
)
def test_invalid_files_are_rejected(tmp_path: Path, content: str, error: str) -> None:
    with pytest.raises(FleetError, match=error):
        load_fleet(write(tmp_path, content), defaults=DEFAULTS)


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(FleetError, match="duplicate ids: cam"):
        load_fleet(write(tmp_path, device(device_id="cam") * 2), defaults=DEFAULTS)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FleetError, match="does not exist"):
        load_fleet(tmp_path / "nope.toml", defaults=DEFAULTS)


def test_errors_never_echo_passwords(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[[devices]]\nid = "a"\nname = "x"\n[devices.source]\nkind = "rtsp"\n'
        'url = "rtsp://admin:hunter2@cam/s"\n',
    )

    with pytest.raises(FleetError) as exc_info:
        load_fleet(path, defaults=DEFAULTS)

    assert "hunter2" not in str(exc_info.value)
