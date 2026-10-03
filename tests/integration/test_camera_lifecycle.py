import asyncio
import logging
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from asgi_lifespan import LifespanManager

from vision_hub.core.config import AppConfig, Environment, Settings, VisionConfig
from vision_hub.core.container import build_container
from vision_hub.domain.devices import DeviceStatus
from vision_hub.infra.process_lock import ProcessLock, ProcessLockError
from vision_hub.main import create_app
from vision_hub.vision.fleet import FleetError

FLEET = """
[[devices]]
id = "front"
name = "Front"
[devices.source]
kind = "synthetic"
fps = 20
visit_every_seconds = 2
visit_seconds = 0.6

[[devices]]
id = "back"
name = "Back"
[devices.source]
kind = "synthetic"
fps = 20

[[devices]]
id = "spare"
name = "Spare"
enabled = false
[devices.source]
kind = "synthetic"
"""


def settings_with(devices_file: Path | None, lock_file: Path) -> Settings:
    return Settings(
        app=AppConfig(env=Environment.TEST),
        vision=VisionConfig(devices_file=devices_file, lock_file=lock_file),
    )


@pytest.fixture
def fleet_settings(tmp_path: Path) -> Settings:
    path = tmp_path / "devices.toml"
    path.write_text(FLEET)
    return settings_with(path, tmp_path / "hub.lock")


def camera_threads() -> list[str]:
    return sorted(t.name for t in threading.enumerate() if t.name.startswith("camera-"))


async def wait_for(condition: Callable[[], bool], within: float = 5) -> None:
    async with asyncio.timeout(within):
        while not condition():
            await asyncio.sleep(0.01)


async def test_enabled_cameras_run_for_the_app_lifetime(fleet_settings: Settings) -> None:
    async with build_container(fleet_settings) as container:
        cameras = container.cameras
        assert camera_threads() == ["camera-back", "camera-front"]
        await asyncio.wait_for(cameras.frames("front").next(), timeout=5)
        await asyncio.wait_for(cameras.frames("back").next(), timeout=5)
        await wait_for(lambda: cameras.status("front") is DeviceStatus.ONLINE)
        assert cameras.is_running("spare") is False
        assert [spec.id for spec in container.fleet] == ["front", "back", "spare"]

    assert camera_threads() == []
    assert cameras.status("front") is DeviceStatus.STOPPED


async def test_cameras_never_block_the_event_loop(
    fleet_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    """asyncio debug mode logs every callback slower than ``slow_callback_duration``."""
    loop = asyncio.get_running_loop()
    loop.set_debug(True)
    loop.slow_callback_duration = 0.05
    caplog.set_level(logging.WARNING, logger="asyncio")
    try:
        async with build_container(fleet_settings) as container:
            for device in ("front", "back"):
                async with container.cameras.frames(device).watching():
                    await asyncio.sleep(0.75)  # encode every frame, as for a live viewer
    finally:
        loop.set_debug(False)

    slow = [r.getMessage() for r in caplog.records if "Executing" in r.getMessage()]
    assert slow == []


async def test_invalid_devices_file_fails_startup(tmp_path: Path) -> None:
    path = tmp_path / "devices.toml"
    path.write_text('[[devices]]\nid = "x"\n')

    with pytest.raises(FleetError):
        async with LifespanManager(create_app(settings_with(path, tmp_path / "hub.lock"))):
            pass

    assert camera_threads() == []


async def test_second_process_is_refused(tmp_path: Path) -> None:
    lock_file = tmp_path / "hub.lock"

    with ProcessLock(lock_file), pytest.raises(ProcessLockError):
        async with build_container(settings_with(None, lock_file)):
            pass


async def test_lock_is_released_on_shutdown(tmp_path: Path) -> None:
    settings = settings_with(None, tmp_path / "hub.lock")

    async with build_container(settings):
        pass

    with ProcessLock(tmp_path / "hub.lock"):
        pass
