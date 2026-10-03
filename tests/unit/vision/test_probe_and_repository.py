from pathlib import Path

import cv2
import numpy as np

from vision_hub.infra.devices import InMemoryDeviceRepository
from vision_hub.vision.fleet import DeviceSpec
from vision_hub.vision.probe import probe_source
from vision_hub.vision.sources import SyntheticSourceConfig, VideoFileSourceConfig


def device(device_id: str) -> DeviceSpec:
    return DeviceSpec(id=device_id, name=device_id, source=SyntheticSourceConfig())


class TestProbe:
    def test_working_source_reports_its_format(self) -> None:
        result = probe_source(SyntheticSourceConfig(width=320, height=240, fps=12))

        assert result.ok is True
        assert (result.width, result.height, result.fps) == (320, 240, 12)
        assert result.error is None
        assert result.elapsed_ms >= 0

    def test_real_video_file(self, tmp_path: Path) -> None:
        path = tmp_path / "clip.avi"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"MJPG"), 8, (80, 60))
        writer.write(np.zeros((60, 80, 3), dtype=np.uint8))
        writer.release()

        result = probe_source(VideoFileSourceConfig(path=path))

        assert result.ok is True
        assert (result.width, result.height, result.fps) == (80, 60, 8)

    def test_failure_is_reported_not_raised(self, tmp_path: Path) -> None:
        result = probe_source(VideoFileSourceConfig(path=tmp_path / "missing.mp4"))

        assert result.ok is False
        assert result.error == "file missing.mp4 does not exist"


class TestInMemoryRepository:
    async def test_crud(self) -> None:
        repository = InMemoryDeviceRepository([device("b"), device("a")])

        assert [d.id for d in await repository.list()] == ["a", "b"]
        assert (await repository.get("a")) == device("a")
        assert await repository.get("zzz") is None

        await repository.save(device("c"))
        assert await repository.delete("a") is True
        assert await repository.delete("a") is False
        assert [d.id for d in await repository.list()] == ["b", "c"]
