"""Restarts and crashes: undelivered alerts, interrupted events, events open at shutdown."""

import asyncio
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from sqlalchemy import select

from vision_hub.core.config import (
    AppConfig,
    Environment,
    NotificationsConfig,
    Settings,
    SmtpConfig,
    VisionConfig,
)
from vision_hub.core.container import Container, build_container
from vision_hub.core.security import utc_now
from vision_hub.domain.history import EventRecord
from vision_hub.domain.motion import MotionEvent
from vision_hub.infra.db.engine import create_engine, create_sessions
from vision_hub.infra.db.models import NotificationRow
from vision_hub.infra.db.repositories.events import SqlEventRepository

type LogRecords = Callable[[], list[dict[str, Any]]]

FLEET = """
[[devices]]
id = "front"
name = "Front door"
[devices.source]
kind = "synthetic"
fps = 20
visit_every_seconds = {every}
visit_seconds = {visit}
[devices.detection]
warmup_frames = 3
motion_end_grace_seconds = 0.3
"""


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def settings(
    tmp_path: Path, *, smtp_port: int | None = None, every: float = 2, visit: float = 0.6
) -> Settings:
    devices = tmp_path / "devices.toml"
    devices.write_text(FLEET.format(every=every, visit=visit))
    smtp = SmtpConfig()
    if smtp_port is not None:
        smtp = SmtpConfig(
            enabled=True,
            host="127.0.0.1",
            port=smtp_port,
            tls="none",
            timeout_seconds=2,
            username="alerts@example.com",
            password=SecretStr("app-password"),
        )
    return Settings(
        app=AppConfig(env=Environment.TEST),
        vision=VisionConfig(
            devices_file=devices, lock_file=tmp_path / "hub.lock", alert_cooldown_seconds=3600
        ),
        smtp=smtp,
        notifications=NotificationsConfig(
            max_attempts=50, retry_initial_seconds=0.05, retry_max_seconds=0.2
        ),
    )


async def outbox_rows(container: Container) -> list[NotificationRow]:
    async with container.sessions() as session:
        return list((await session.scalars(select(NotificationRow))).all())


async def eventually[T](probe: Callable[[], Awaitable[T]], within: float = 15) -> T:
    async with asyncio.timeout(within):
        while not (result := await probe()):
            await asyncio.sleep(0.05)
    return result


async def test_undelivered_alerts_are_sent_after_a_restart(
    tmp_path: Path, smtp_server: Any
) -> None:
    # First run: the mail server is down, so the alert stays pending in the outbox.
    async with build_container(settings(tmp_path, smtp_port=unused_port())) as first:

        async def failed_once() -> list[NotificationRow]:
            return [row for row in await outbox_rows(first) if row.attempts >= 1]

        [pending] = await eventually(failed_once)
    assert pending.status == "pending"
    assert pending.last_error is not None

    # Second run: the mail server is back; the stored alert is delivered, and the restored
    # cooldown keeps the camera's new events from alerting again.
    async with build_container(settings(tmp_path, smtp_port=smtp_server.port)) as second:
        [mail] = await smtp_server.wait_for_mail(1, within=15)

        async def sent() -> list[NotificationRow]:
            return [row for row in await outbox_rows(second) if row.status == "sent"]

        [delivered] = await eventually(sent)
        await asyncio.sleep(2.5)  # the camera records at least one more event meanwhile
        assert len(await outbox_rows(second)) == 1

    assert mail.message["X-Vision-Hub-Event"] == pending.event_id
    assert delivered.id == pending.id
    assert delivered.attempts > pending.attempts


async def test_events_open_after_a_crash_are_flagged_on_startup(
    tmp_path: Path, log_records: LogRecords
) -> None:
    config = settings(tmp_path)
    engine = create_engine(config.db)
    try:
        await SqlEventRepository(create_sessions(engine)).add_started(
            MotionEvent(
                id="00000000-0000-7000-8000-000000000001", device_id="front", started_at=utc_now()
            )
        )
    finally:
        await engine.dispose()

    async with build_container(config) as container:
        record = await container.events.get("00000000-0000-7000-8000-000000000001")

    assert (record.interrupted, record.complete) == (True, False)
    recovered = next(r for r in log_records() if r["event"] == "interrupted_events_recovered")
    assert recovered["count"] == 1


async def test_events_in_progress_are_closed_and_stored_on_shutdown(tmp_path: Path) -> None:
    config = settings(tmp_path, every=2, visit=1.9)  # long visits: motion is ongoing at exit

    async with build_container(config) as container:

        async def started() -> list[EventRecord]:
            return list(await container.events.list(limit=5))

        [open_event] = await eventually(started)
        assert open_event.complete is False

    async with build_container(config) as restarted:
        record = await restarted.events.get(open_event.event.id)

    assert record.complete is True  # cameras stop before the recorder drains
    assert record.interrupted is False
    assert record.snapshots  # evidence was written on the way down
