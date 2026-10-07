"""End to end with the real model: a people-only camera alerts when a person appears and stays
quiet for other motion. Skipped until `vision-hub download-model` has been run."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import cv2
import httpx2
import numpy as np
import pytest
from asgi_lifespan import LifespanManager
from pydantic import SecretStr

from vision_hub.core.config import (
    AppConfig,
    ClipConfig,
    Environment,
    PersonConfig,
    SecurityConfig,
    Settings,
    SmtpConfig,
    VisionConfig,
)
from vision_hub.main import create_app

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "data" / "models" / "person-yolox-s.onnx"
PHOTO = ROOT / "tests" / "assets" / "person.jpg"

pytestmark = pytest.mark.skipif(not MODEL.is_file(), reason="run `vision-hub download-model` first")

FLEET = """
[[devices]]
id = "yard"
name = "Yard"
[devices.source]
kind = "synthetic"
fps = 20
visit_every_seconds = 2.5
visit_seconds = 0.8
[devices.detection]
warmup_frames = 3
motion_end_grace_seconds = 0.4
alert_on = "person"

[[devices]]
id = "door"
name = "Door"
[devices.source]
kind = "video_file"
path = "{video}"
[devices.detection]
warmup_frames = 3
motion_end_grace_seconds = 0.4
alert_on = "person"
"""


def person_walks_in(path: Path, fps: int = 10) -> None:
    """An empty doorway, then someone standing in it for two seconds, then empty again."""
    rng = np.random.default_rng(5)
    background = cv2.GaussianBlur(rng.integers(60, 200, (480, 640, 3), dtype=np.uint8), (0, 0), 9)
    photo = cv2.imread(str(PHOTO))
    assert photo is not None
    person = cv2.resize(photo, (300, 352))
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"mp4v"), fps, (640, 480))
    for second in range(6):
        for _ in range(fps):
            frame = background.copy()
            if second in (2, 3):
                frame[100:452, 170:470] = person
            writer.write(frame)
    writer.release()


@pytest.fixture
async def hub(
    tmp_path: Path, admin_password_hash: SecretStr, smtp_server: Any
) -> AsyncIterator[httpx2.AsyncClient]:
    video = tmp_path / "door.mp4"
    person_walks_in(video)
    devices = tmp_path / "devices.toml"
    devices.write_text(FLEET.format(video=video.as_posix()))
    settings = Settings(
        app=AppConfig(env=Environment.TEST),
        security=SecurityConfig(admin_password_hash=admin_password_hash),
        vision=VisionConfig(
            devices_file=devices, lock_file=tmp_path / "hub.lock", alert_cooldown_seconds=0
        ),
        persons=PersonConfig(model_path=MODEL, check_interval_seconds=0.3),
        clips=ClipConfig(enabled=False),
        smtp=SmtpConfig(
            enabled=True,
            host="127.0.0.1",
            port=smtp_server.port,
            tls="none",
            timeout_seconds=2,
            username="alerts@example.com",
            password=SecretStr("app-password"),
        ),
    )
    async with LifespanManager(create_app(settings)) as manager:
        transport = httpx2.ASGITransport(app=manager.app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as http:
            yield http


async def test_people_only_cameras_alert_for_people_and_nothing_else(
    hub: httpx2.AsyncClient, admin_credentials: dict[str, str], smtp_server: Any
) -> None:
    token = (await hub.post("/api/v1/auth/token", data=admin_credentials)).json()
    headers = {"Authorization": f"Bearer {token['access_token']}"}

    async def finished(device: str) -> list[dict[str, Any]]:
        response = await hub.get("/api/v1/events", params={"device_id": device}, headers=headers)
        return [e for e in response.json()["items"] if e["complete"]]

    async with asyncio.timeout(30):
        while len(await finished("yard")) < 2 or not await finished("door"):
            await asyncio.sleep(0.25)
        [mail, *_] = await smtp_server.wait_for_mail(1)

    yard, door = await finished("yard"), await finished("door")
    assert all(e["person"] is False and e["alert"] is False for e in yard)
    assert door[0]["person"] is True
    assert door[0]["person_confidence"] > 0.5
    assert door[0]["alert"] is True
    assert mail.message["Subject"] == "Person detected: Door"
    # Only the door alerted: every mail so far is about it.
    assert all(m.message["Subject"] == "Person detected: Door" for m in smtp_server.received)

    people = await hub.get("/api/v1/events", params={"person": "true"}, headers=headers)
    assert {e["device_id"] for e in people.json()["items"]} == {"door"}
