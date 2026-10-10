import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from vision_hub.domain.auth import Principal, Role
from vision_hub.domain.motion import BoundingBox
from vision_hub.infra.auth import InMemoryTicketStore
from vision_hub.realtime.mjpeg import BOUNDARY, MEDIA_TYPE, detections, mjpeg_stream, part
from vision_hub.vision.bridge import FramePacket, LatestFrame

T0 = datetime(2026, 1, 1, tzinfo=UTC)
USER = Principal("admin", Role.ADMIN)


def packet(sequence: int) -> FramePacket:
    return FramePacket(
        device_id="cam",
        sequence=sequence,
        captured_at=T0,
        width=2,
        height=2,
        motion=False,
        jpeg=f"jpeg-{sequence}".encode(),
    )


class TestMjpeg:
    def test_part_format(self) -> None:
        assert part(b"abc") == (
            b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 3\r\n\r\nabc\r\n"
        )
        assert f"multipart/x-mixed-replace; boundary={BOUNDARY}" == MEDIA_TYPE

    def test_parts_with_detections_carry_them_in_a_header(self) -> None:
        assert part(b"abc", '{"boxes":[]}') == (
            b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 3\r\n"
            b'X-Detections: {"boxes":[]}\r\n\r\nabc\r\n'
        )

    def test_detections_are_fractions_of_the_picture(self) -> None:
        moving = replace(
            packet(1),
            width=640,
            height=480,
            boxes=(BoundingBox(x=320, y=120, width=64, height=240),),
            person=0.8731,
        )

        assert detections(moving) == '{"boxes":[[0.5,0.25,0.1,0.5]],"person":0.87}'
        assert detections(replace(moving, person=None)) == (
            '{"boxes":[[0.5,0.25,0.1,0.5]],"person":null}'
        )
        assert detections(replace(moving, boxes=())) == '{"boxes":[],"person":0.87}'

    def test_frames_without_detections_have_no_header(self) -> None:
        assert detections(packet(1)) is None

    async def test_stream_parts_carry_the_detections(self) -> None:
        frames = LatestFrame()
        frames.publish(replace(packet(1), boxes=(BoundingBox(x=0, y=0, width=1, height=1),)))
        stream = mjpeg_stream(frames, max_fps=100, still_running=lambda: True)

        first = await anext(stream)
        await stream.aclose()

        assert b'X-Detections: {"boxes":[[0.0,0.0,0.5,0.5]],"person":null}\r\n' in first

    async def test_stream_ends_when_frames_are_closed(self) -> None:
        frames = LatestFrame()
        frames.publish(packet(1))
        stream = mjpeg_stream(frames, max_fps=100, still_running=lambda: True)
        await anext(stream)
        pending = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0)

        frames.close()  # the hub is shutting down

        with pytest.raises(StopAsyncIteration):
            await pending
        assert frames.viewers == 0

    async def test_streams_each_new_frame_once_and_counts_as_a_viewer(self) -> None:
        frames = LatestFrame()
        frames.publish(packet(1))
        stream = mjpeg_stream(frames, max_fps=100, still_running=lambda: True)

        first = await anext(stream)
        assert frames.viewers == 1
        pending = asyncio.ensure_future(anext(stream))
        await asyncio.sleep(0.05)
        assert not pending.done()  # no new frame: nothing is re-sent
        frames.publish(packet(2))
        second = await pending
        await stream.aclose()

        assert first.endswith(b"jpeg-1\r\n")
        assert second.endswith(b"jpeg-2\r\n")
        assert frames.viewers == 0

    async def test_frame_rate_is_capped(self) -> None:
        frames = LatestFrame()
        stream = mjpeg_stream(frames, max_fps=10, still_running=lambda: True)

        async def producer() -> None:
            for sequence in range(1, 100):
                frames.publish(packet(sequence))
                await asyncio.sleep(0.005)

        task = asyncio.create_task(producer())
        started = time.monotonic()
        for _ in range(4):
            await anext(stream)
        elapsed = time.monotonic() - started
        await stream.aclose()
        task.cancel()

        assert elapsed >= 0.28  # 3 intervals at 10 fps, despite ~200 fps of input

    async def test_slow_consumers_are_not_delayed_further(self) -> None:
        frames = LatestFrame()
        frames.publish(packet(1))
        stream = mjpeg_stream(frames, max_fps=100, still_running=lambda: True)
        await anext(stream)

        await asyncio.sleep(0.05)  # consumer slower than the 10 ms frame interval
        frames.publish(packet(2))
        started = time.monotonic()
        await anext(stream)
        await stream.aclose()

        assert time.monotonic() - started < 0.05

    async def test_ends_when_the_camera_stops(self) -> None:
        frames = LatestFrame()
        running = [True]
        stream = mjpeg_stream(
            frames, max_fps=100, still_running=lambda: running[0], stall_timeout=0.05
        )
        pending = asyncio.ensure_future(anext(stream, None))
        await asyncio.sleep(0.1)  # stalls once while running: keeps waiting
        assert not pending.done()

        running[0] = False

        assert await asyncio.wait_for(pending, 1) is None
        assert frames.viewers == 0


class TestTicketStore:
    def test_tickets_are_single_use(self) -> None:
        store = InMemoryTicketStore(ttl_seconds=30)
        ticket = store.issue(USER)

        assert store.consume(ticket) == USER
        assert store.consume(ticket) is None
        assert store.consume("never-issued") is None
        assert store.ttl_seconds == 30

    def test_tickets_expire(self) -> None:
        now = [T0]
        store = InMemoryTicketStore(ttl_seconds=30, clock=lambda: now[0])
        ticket = store.issue(USER)

        now[0] += timedelta(seconds=31)

        assert store.consume(ticket) is None

    def test_expired_tickets_are_purged_on_issue(self) -> None:
        now = [T0]
        store = InMemoryTicketStore(ttl_seconds=30, clock=lambda: now[0])
        store.issue(USER)
        store.issue(USER)

        now[0] += timedelta(seconds=31)
        store.issue(USER)

        assert len(store) == 1

    def test_tickets_are_unguessable(self) -> None:
        store = InMemoryTicketStore(ttl_seconds=30)

        tickets = {store.issue(USER) for _ in range(100)}

        assert len(tickets) == 100
        assert all(len(ticket) >= 40 for ticket in tickets)
