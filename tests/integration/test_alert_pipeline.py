"""End to end: synthetic camera -> detector -> tracker -> bus -> notification service -> SMTP."""

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pydantic import SecretStr

from vision_hub.core.config import AppConfig, Environment, Settings, SmtpConfig, VisionConfig
from vision_hub.core.container import build_container

FLEET = """
[[devices]]
id = "front"
name = "Front door"
[devices.source]
kind = "synthetic"
fps = 20
visit_every_seconds = 2
visit_seconds = 0.6
[devices.detection]
warmup_frames = 3
motion_end_grace_seconds = 0.3
"""


async def test_motion_on_a_camera_arrives_as_an_email(tmp_path: Path, smtp_server: Any) -> None:
    devices = tmp_path / "devices.toml"
    devices.write_text(FLEET)
    settings = Settings(
        app=AppConfig(env=Environment.TEST),
        vision=VisionConfig(
            devices_file=devices, lock_file=tmp_path / "hub.lock", alert_cooldown_seconds=0
        ),
        smtp=SmtpConfig(
            enabled=True,
            host="127.0.0.1",
            port=smtp_server.port,
            tls="none",
            username="alerts@example.com",
            password=SecretStr("app-password"),
        ),
    )

    async with build_container(settings) as container:
        assert container.notifications.enabled
        [mail] = (await smtp_server.wait_for_mail(1, within=15))[:1]

    message = mail.message
    assert message["Subject"] == "Motion detected: Front door"
    assert mail.recipients == ["alerts@example.com"]  # defaults to the sender
    [attachment] = list(message.iter_attachments())
    image = cv2.imdecode(np.frombuffer(attachment.get_content(), np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    assert image.shape == (480, 640, 3)
