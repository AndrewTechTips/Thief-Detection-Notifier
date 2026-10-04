import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from starlette.types import Message

from vision_hub.core.config import RealtimeConfig
from vision_hub.core.metrics import Metrics
from vision_hub.domain.auth import Principal, Role
from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.events import (
    CameraEvent,
    DeviceStatusChanged,
    MotionEndedEvent,
    MotionStartedEvent,
    topic_for,
)
from vision_hub.domain.history import EventRecord
from vision_hub.domain.motion import BoundingBox, MotionEvent
from vision_hub.infra.bus.memory import InMemoryEventBus
from vision_hub.realtime.connections import CloseCode, ConnectionManager

T0 = datetime(2026, 1, 1, tzinfo=UTC)
USER = Principal("admin", Role.ADMIN)


class FakeSocket:
    def __init__(self, *, send_delay: float = 0) -> None:
        self.sent: list[dict[str, Any]] = []
        self.incoming: asyncio.Queue[Message] = asyncio.Queue()
        self.closed: tuple[int, str | None] | None = None
        self.send_delay = send_delay
        self.message_arrived = asyncio.Event()

    async def send_text(self, data: str) -> None:
        await asyncio.sleep(self.send_delay)
        self.sent.append(json.loads(data))
        self.message_arrived.set()

    async def receive(self) -> Message:
        return await self.incoming.get()

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (code, reason)

    def say(self, payload: object) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        self.incoming.put_nowait({"type": "websocket.receive", "text": text})

    def leave(self) -> None:
        self.incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})

    def types(self) -> list[str]:
        return [m["type"] for m in self.sent]

    async def wait_for(self, message_type: str, within: float = 2) -> dict[str, Any]:
        async with asyncio.timeout(within):
            while True:
                for message in self.sent:
                    if message["type"] == message_type:
                        return message
                self.message_arrived.clear()
                await self.message_arrived.wait()


def status(device_id: str, value: DeviceStatus = DeviceStatus.ONLINE) -> CameraEvent:
    return DeviceStatusChanged(device_id=device_id, status=value, at=T0)


def motion(device_id: str) -> MotionEvent:
    return MotionEvent(id=f"evt-{device_id}", device_id=device_id, started_at=T0, ended_at=T0)


class Harness:
    def __init__(self, **config: Any) -> None:
        self.bus: InMemoryEventBus[CameraEvent] = InMemoryEventBus()
        self.metrics = Metrics(process_metrics=False)
        self.manager = ConnectionManager(self.bus, RealtimeConfig(**config), metrics=self.metrics)
        self.socket = FakeSocket()
        self.task: asyncio.Task[None] | None = None

    async def connect(self, socket: FakeSocket | None = None) -> FakeSocket:
        self.socket = socket or self.socket
        self.task = asyncio.create_task(self.manager.serve(self.socket, USER))
        await self.socket.wait_for("subscription")
        return self.socket

    def publish(self, event: CameraEvent) -> None:
        self.bus.publish(topic_for(event), event)

    async def finished(self, within: float = 2) -> None:
        assert self.task is not None
        await asyncio.wait_for(self.task, within)


class TestForwarding:
    async def test_starts_with_a_subscription_to_everything(self) -> None:
        harness = Harness()
        socket = await harness.connect()

        ack = socket.sent[0]
        assert ack["type"] == "subscription"
        assert ack["data"] == {"devices": None, "excluded": []}
        assert ack["v"] == 1
        assert harness.manager.active == 1
        socket.leave()
        await harness.finished()

    async def test_events_become_typed_messages(self) -> None:
        harness = Harness()
        socket = await harness.connect()

        harness.publish(status("porch"))
        harness.publish(MotionStartedEvent(event=motion("porch")))
        harness.publish(
            MotionEndedEvent(
                event=motion("porch"),
                snapshot_jpeg=b"x",
                annotated_jpeg=b"y",
                boxes=(BoundingBox(1, 2, 3, 4),),
            )
        )
        ended = await socket.wait_for("motion.ended")

        assert socket.types()[1:] == ["device.status", "motion.started", "motion.ended"]
        assert socket.sent[1]["device_id"] == "porch"
        assert socket.sent[1]["data"] == {"status": "online"}
        assert ended["data"]["event_id"] == "evt-porch"
        assert ended["data"]["boxes"] == [{"x": 1, "y": 2, "width": 3, "height": 4}]
        assert "snapshot_jpeg" not in json.dumps(ended)  # images are not pushed over the socket
        socket.leave()
        await harness.finished()

    async def test_client_leaving_ends_the_session_cleanly(self) -> None:
        harness = Harness()
        socket = await harness.connect()

        socket.leave()
        await harness.finished()

        assert socket.closed is None  # the client already closed; nothing to send
        assert harness.manager.active == 0
        assert harness.bus.subscriber_count == 0


class TestSubscriptions:
    async def test_selecting_devices(self) -> None:
        harness = Harness()
        socket = await harness.connect()

        socket.say({"type": "subscribe", "devices": ["gate"]})
        ack = await socket.wait_for("subscription")
        while ack["data"]["devices"] != ["gate"]:
            ack = socket.sent[-1]
            await asyncio.sleep(0.01)
        harness.publish(status("porch"))
        harness.publish(status("gate"))
        message = await socket.wait_for("device.status")

        assert message["device_id"] == "gate"
        assert [m["device_id"] for m in socket.sent if m["type"] == "device.status"] == ["gate"]
        socket.leave()
        await harness.finished()

    async def test_unsubscribing_while_subscribed_to_all_excludes(self) -> None:
        harness = Harness()
        socket = await harness.connect()

        socket.say({"type": "unsubscribe", "devices": ["porch"]})
        await asyncio.sleep(0.05)

        assert socket.sent[-1]["data"] == {"devices": None, "excluded": ["porch"]}
        harness.publish(status("porch"))
        harness.publish(status("gate"))
        message = await socket.wait_for("device.status")
        assert message["device_id"] == "gate"
        socket.leave()
        await harness.finished()

    async def test_unsubscribing_from_a_selection_removes(self) -> None:
        harness = Harness()
        socket = await harness.connect()

        socket.say({"type": "subscribe", "devices": ["a", "b"]})
        socket.say({"type": "unsubscribe", "devices": ["a"]})
        await asyncio.sleep(0.05)

        assert socket.sent[-1]["data"] == {"devices": ["b"], "excluded": []}
        socket.say({"type": "subscribe"})
        await asyncio.sleep(0.05)
        assert socket.sent[-1]["data"] == {"devices": None, "excluded": []}
        socket.leave()
        await harness.finished()

    @pytest.mark.parametrize(
        ("payload", "code"),
        [
            ("{not json", "invalid_json"),
            ({"type": "teleport"}, "invalid_message"),
            ({"type": "subscribe", "devices": "porch"}, "invalid_message"),
        ],
    )
    async def test_bad_messages_get_an_error_and_the_session_continues(
        self, payload: object, code: str
    ) -> None:
        harness = Harness()
        socket = await harness.connect()

        socket.say(payload)
        error = await socket.wait_for("error")
        harness.publish(status("porch"))
        await socket.wait_for("device.status")

        assert error["data"]["code"] == code
        socket.leave()
        await harness.finished()

    async def test_binary_frames_are_rejected_politely(self) -> None:
        harness = Harness()
        socket = await harness.connect()

        socket.incoming.put_nowait({"type": "websocket.receive", "bytes": b"\x00"})
        error = await socket.wait_for("error")

        assert error["data"]["code"] == "unsupported"
        socket.leave()
        await harness.finished()


class TestHeartbeatAndBackPressure:
    async def test_pings_are_sent_and_pongs_keep_the_session_alive(self) -> None:
        harness = Harness(ping_interval_seconds=0.05, idle_timeout_seconds=0.12)
        socket = await harness.connect()

        for _ in range(5):
            socket.say({"type": "pong"})
            await asyncio.sleep(0.05)

        assert socket.types().count("ping") >= 3
        assert socket.closed is None
        socket.leave()
        await harness.finished()

    async def test_silent_clients_time_out(self) -> None:
        harness = Harness(ping_interval_seconds=0.05, idle_timeout_seconds=0.1)
        socket = await harness.connect()

        await harness.finished()

        assert socket.closed == (CloseCode.IDLE_TIMEOUT, "idle timeout")

    async def test_clients_that_fall_behind_are_disconnected(self) -> None:
        harness = Harness(client_queue_size=2)
        socket = await harness.connect()
        socket.send_delay = 0.2  # the network to this client is congested

        for n in range(10):
            harness.publish(status(f"cam-{n}"))
        await harness.finished(within=5)

        assert socket.closed == (CloseCode.TRY_AGAIN_LATER, "client too slow")
        disconnects = harness.metrics.registry.get_sample_value(
            "vision_hub_websocket_disconnects_total", {"code": "1013"}
        )
        assert disconnects == 1

    async def test_stalled_sends_time_out(self) -> None:
        harness = Harness(send_timeout_seconds=0.05)
        socket = await harness.connect()
        socket.send_delay = 1

        harness.publish(status("porch"))
        await harness.finished()

        assert socket.closed == (CloseCode.TRY_AGAIN_LATER, "send timed out")


async def test_shutdown_closes_every_client_with_going_away() -> None:
    harness = Harness()
    first = await harness.connect()
    second_task = asyncio.create_task(harness.manager.serve(second := FakeSocket(), USER))
    await second.wait_for("subscription")

    harness.manager.close_all()
    await harness.finished()
    await asyncio.wait_for(second_task, 2)

    assert first.closed == second.closed == (CloseCode.GOING_AWAY, "server shutting down")


async def test_unexpected_errors_close_with_internal_error() -> None:
    class BrokenSocket(FakeSocket):
        async def receive(self) -> Message:
            raise ZeroDivisionError("bug in the reader")

    harness = Harness()
    socket = BrokenSocket()
    harness.task = asyncio.create_task(harness.manager.serve(socket, USER))

    await harness.finished()

    assert socket.closed == (CloseCode.INTERNAL_ERROR, "internal error")
    assert harness.manager.active == 0


async def test_failing_first_send_closes_the_connection() -> None:
    harness = Harness(send_timeout_seconds=0.05)
    socket = FakeSocket(send_delay=1)
    harness.task = asyncio.create_task(harness.manager.serve(socket, USER))

    await harness.finished()

    assert socket.closed == (CloseCode.TRY_AGAIN_LATER, "send timed out")


class FakeHistory:
    """Stands in for EventService.after(): events newer than an id, oldest first."""

    def __init__(self, records: list[EventRecord]) -> None:
        self.records = records

    async def after(self, event_id: str, *, limit: int) -> list[EventRecord]:
        return [r for r in self.records if r.event.id > event_id][:limit]


def record(
    n: int, device_id: str = "porch", *, complete: bool = True, interrupted: bool = False
) -> EventRecord:
    event = MotionEvent(
        id=f"00000000-0000-7000-8000-{n:012d}",
        device_id=device_id,
        started_at=T0,
        ended_at=T0 if complete else None,
    )
    return EventRecord(event=event, interrupted=interrupted)


class TestResume:
    async def test_replays_missed_events_then_reports_done(self) -> None:
        records = [
            record(1),
            record(2, "gate"),
            record(3, complete=False),
            record(4, complete=False, interrupted=True),
        ]
        harness = Harness()
        harness.manager = ConnectionManager(
            harness.bus,
            RealtimeConfig(),
            history=FakeHistory(records),  # type: ignore[arg-type]
        )
        socket = await harness.connect()

        socket.say({"type": "resume", "after": "00000000-0000-7000-8000-000000000000"})
        done = await socket.wait_for("replay.done")

        replayed = [m for m in socket.sent if m.get("replay")]
        assert [(m["type"], m["device_id"]) for m in replayed] == [
            ("motion.ended", "porch"),
            ("motion.ended", "gate"),
            ("motion.started", "porch"),  # still in progress
        ]  # the interrupted event is skipped: its start would show motion forever
        assert done["data"] == {"count": 3, "truncated": False}
        socket.leave()
        await harness.finished()

    async def test_replay_respects_the_subscription(self) -> None:
        harness = Harness()
        harness.manager = ConnectionManager(
            harness.bus,
            RealtimeConfig(),
            history=FakeHistory([record(1), record(2, "gate")]),  # type: ignore[arg-type]
        )
        socket = await harness.connect()

        socket.say({"type": "subscribe", "devices": ["gate"]})
        socket.say({"type": "resume", "after": "00000000-0000-7000-8000-000000000000"})
        done = await socket.wait_for("replay.done")

        assert [m["device_id"] for m in socket.sent if m.get("replay")] == ["gate"]
        assert done["data"]["count"] == 1
        socket.leave()
        await harness.finished()

    async def test_large_gaps_are_truncated(self) -> None:
        harness = Harness()
        harness.manager = ConnectionManager(
            harness.bus,
            RealtimeConfig(),
            history=FakeHistory([record(n) for n in range(1, 150)]),  # type: ignore[arg-type]
        )
        socket = await harness.connect()

        socket.say({"type": "resume", "after": "00000000-0000-7000-8000-000000000000"})
        done = await socket.wait_for("replay.done")

        assert done["data"] == {"count": 100, "truncated": True}
        socket.leave()
        await harness.finished()

    async def test_without_history_resume_is_an_error(self) -> None:
        harness = Harness()
        socket = await harness.connect()

        socket.say({"type": "resume", "after": "00000000-0000-7000-8000-000000000000"})
        error = await socket.wait_for("error")

        assert error["data"]["code"] == "unsupported"
        socket.leave()
        await harness.finished()

    async def test_resume_needs_a_valid_event_id(self) -> None:
        harness = Harness()
        socket = await harness.connect()

        socket.say({"type": "resume", "after": "not-a-uuid"})
        error = await socket.wait_for("error")

        assert error["data"]["code"] == "invalid_message"
        socket.leave()
        await harness.finished()
