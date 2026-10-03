import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr

from vision_hub.core.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)
from vision_hub.infra.devices import InMemoryDeviceRepository
from vision_hub.services import devices as devices_module
from vision_hub.services.devices import DeviceService
from vision_hub.vision.bridge import FramePacket
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.fleet import DeviceSpec
from vision_hub.vision.manager import CameraManager, Worker
from vision_hub.vision.probe import ProbeResult
from vision_hub.vision.sources import (
    RtspSourceConfig,
    SourceConfig,
    SyntheticSourceConfig,
    VideoFileSourceConfig,
)
from vision_hub.vision.worker import WorkerSink


class FakeWorker:
    def __init__(self) -> None:
        self.alive = False

    @property
    def is_alive(self) -> bool:
        return self.alive

    def start(self) -> None:
        self.alive = True

    def stop(self, timeout: float = 5.0) -> bool:
        self.alive = False
        return True


class Factory:
    def __init__(self) -> None:
        self.built: list[DeviceSpec] = []

    def __call__(self, spec: DeviceSpec, _sink: WorkerSink) -> Worker:
        self.built.append(spec)
        return FakeWorker()


def spec(
    device_id: str = "porch", *, enabled: bool = True, source: SourceConfig | None = None
) -> DeviceSpec:
    return DeviceSpec(
        id=device_id,
        name=device_id.title(),
        enabled=enabled,
        source=source or SyntheticSourceConfig(),
    )


@pytest.fixture
def factory() -> Factory:
    return Factory()


@pytest.fixture
def cameras(factory: Factory) -> CameraManager:
    return CameraManager(worker_factory=factory)


@pytest.fixture
def media_dir(tmp_path: Path) -> Path:
    media = tmp_path / "media"
    media.mkdir()
    (media / "clip.mp4").write_bytes(b"")
    return media


@pytest.fixture
def service(cameras: CameraManager, media_dir: Path) -> DeviceService:
    repository = InMemoryDeviceRepository([spec("porch"), spec("gate", enabled=False)])
    return DeviceService(
        repository, cameras, detection_defaults=DetectionConfig(), media_dir=media_dir
    )


class TestLifecycle:
    async def test_start_enabled_runs_only_enabled_devices(
        self, service: DeviceService, cameras: CameraManager
    ) -> None:
        await service.start_enabled()

        assert cameras.is_running("porch") is True
        assert cameras.is_running("gate") is False
        assert service.name_of("porch") == "Porch"
        assert service.name_of("unknown") == "unknown"

    async def test_create_starts_enabled_devices(
        self, service: DeviceService, cameras: CameraManager
    ) -> None:
        view = await service.create(spec("door"))

        assert view.running is True
        assert cameras.is_running("door")
        assert service.name_of("door") == "Door"

    async def test_create_rejects_duplicates(self, service: DeviceService) -> None:
        with pytest.raises(ConflictError, match="already exists"):
            await service.create(spec("porch"))

    async def test_delete_stops_and_forgets(
        self, service: DeviceService, cameras: CameraManager
    ) -> None:
        await service.start_enabled()

        await service.delete("porch")

        assert cameras.is_running("porch") is False
        with pytest.raises(NotFoundError):
            await service.get("porch")
        with pytest.raises(NotFoundError):
            await service.delete("porch")

    async def test_reports_whether_a_camera_runs(self, service: DeviceService) -> None:
        await service.start_enabled()

        assert service.is_running("porch") is True
        assert service.is_running("gate") is False
        assert (await service.live_frames("porch")).latest is None

    async def test_start_and_stop_are_idempotent(
        self, service: DeviceService, factory: Factory
    ) -> None:
        await service.start("gate")
        await service.start("gate")
        assert len(factory.built) == 1

        await service.stop("gate")
        view = await service.stop("gate")
        assert view.running is False


class TestUpdates:
    async def test_cosmetic_changes_do_not_restart(
        self, service: DeviceService, factory: Factory
    ) -> None:
        await service.start_enabled()

        view = await service.update("porch", {"name": "Front porch"})

        assert view.spec.name == "Front porch"
        assert service.name_of("porch") == "Front porch"
        assert len(factory.built) == 1

    async def test_capture_changes_restart_a_running_camera(
        self, service: DeviceService, factory: Factory
    ) -> None:
        await service.start_enabled()
        sensitive = DetectionConfig(min_motion_area=0.002)

        await service.set_detection("porch", sensitive)

        assert len(factory.built) == 2
        assert factory.built[-1].detection == sensitive

    async def test_changes_to_a_stopped_camera_do_not_start_it(
        self, service: DeviceService, cameras: CameraManager
    ) -> None:
        await service.update("gate", {"target_fps": 5})

        assert cameras.is_running("gate") is False

    async def test_enabling_starts_and_disabling_stops(
        self, service: DeviceService, cameras: CameraManager
    ) -> None:
        await service.update("gate", {"enabled": True})
        assert cameras.is_running("gate") is True

        await service.update("gate", {"enabled": False})
        assert cameras.is_running("gate") is False

    async def test_enabling_a_manually_started_camera_keeps_it_running(
        self, service: DeviceService, factory: Factory
    ) -> None:
        await service.start("gate")

        view = await service.update("gate", {"enabled": True})

        assert view.running is True
        assert len(factory.built) == 1  # not restarted

    async def test_secret_survives_unrelated_updates(self, service: DeviceService) -> None:
        """Regression guard: the password is excluded from serialization, so updates must not
        rebuild the source through a dump/validate round trip."""
        rtsp = RtspSourceConfig(url="rtsp://cam/s", username="u", password=SecretStr("hunter2"))
        await service.create(spec("cam", enabled=False, source=rtsp))

        view = await service.update("cam", {"name": "Renamed"})

        assert isinstance(view.spec.source, RtspSourceConfig)
        assert view.spec.source.password is not None
        assert view.spec.source.password.get_secret_value() == "hunter2"

    async def test_unknown_device(self, service: DeviceService) -> None:
        with pytest.raises(NotFoundError, match="ghost"):
            await service.update("ghost", {"name": "x"})


class TestMediaDirectory:
    async def test_relative_paths_resolve_inside(
        self, service: DeviceService, media_dir: Path
    ) -> None:
        view = await service.create(
            spec("file", enabled=False, source=VideoFileSourceConfig(path=Path("clip.mp4")))
        )

        assert isinstance(view.spec.source, VideoFileSourceConfig)
        assert view.spec.source.path == (media_dir / "clip.mp4").resolve()

    @pytest.mark.parametrize("path", [Path("/etc/passwd"), Path("../secrets.mp4")])
    async def test_paths_outside_are_rejected(self, service: DeviceService, path: Path) -> None:
        with pytest.raises(BadRequestError, match="media directory"):
            await service.create(spec("file", source=VideoFileSourceConfig(path=path)))

    async def test_symlinks_out_of_the_directory_are_rejected(
        self, service: DeviceService, media_dir: Path, tmp_path: Path
    ) -> None:
        outside = tmp_path / "private.mp4"
        outside.write_bytes(b"")
        (media_dir / "innocent.mp4").symlink_to(outside)

        with pytest.raises(BadRequestError):
            await service.update(
                "gate", {"source": VideoFileSourceConfig(path=Path("innocent.mp4"))}
            )


class TestSnapshots:
    async def test_stopped_camera_has_no_snapshot(self, service: DeviceService) -> None:
        with pytest.raises(ServiceUnavailableError, match="not running"):
            await service.snapshot("gate")

    async def test_camera_without_frames_asks_to_retry(self, service: DeviceService) -> None:
        await service.start_enabled()

        with pytest.raises(ServiceUnavailableError) as exc_info:
            await service.snapshot("porch")

        assert exc_info.value.response_headers["Retry-After"] == "1"

    async def test_returns_the_latest_frame(
        self, service: DeviceService, cameras: CameraManager
    ) -> None:
        await service.start_enabled()
        packet = FramePacket(
            device_id="porch",
            sequence=1,
            captured_at=datetime.now(UTC),
            width=4,
            height=3,
            motion=False,
            jpeg=b"jpeg",
        )
        cameras.frames("porch").publish(packet)

        assert await service.snapshot("porch") == packet
        assert (await service.get("porch")).latest_frame == packet


class TestSourceTests:
    async def test_probes_a_working_source(self, service: DeviceService) -> None:
        result = await service.test_source(SyntheticSourceConfig(width=320, height=240))

        assert result.ok is True
        assert (result.width, result.height) == (320, 240)

    async def test_slow_sources_time_out(
        self, cameras: CameraManager, media_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def stuck(_config: SourceConfig) -> ProbeResult:
            time.sleep(0.5)
            return ProbeResult(ok=True, elapsed_ms=500)

        monkeypatch.setattr(devices_module, "probe_source", stuck)
        service = DeviceService(
            InMemoryDeviceRepository(),
            cameras,
            detection_defaults=DetectionConfig(),
            media_dir=media_dir,
            probe_timeout=0.05,
        )

        result = await service.test_source(SyntheticSourceConfig())

        assert result.ok is False
        assert result.error == "no frame within 0.05 s"
        await asyncio.sleep(0.5)  # let the worker thread finish before the test ends

    async def test_media_rules_apply_to_probes(self, service: DeviceService) -> None:
        with pytest.raises(BadRequestError):
            await service.test_source(VideoFileSourceConfig(path=Path("/etc/hosts")))
