import asyncio
import threading
from datetime import UTC, datetime

from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.events import CameraEvent, DeviceStatusChanged
from vision_hub.vision.bridge import FramePacket, LatestFrame, LoopBridge

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def packet(sequence: int) -> FramePacket:
    return FramePacket(
        device_id="cam",
        sequence=sequence,
        captured_at=T0,
        width=2,
        height=2,
        motion=False,
        jpeg=b"x",
    )


class TestLatestFrame:
    async def test_next_waits_for_the_first_frame(self) -> None:
        frames = LatestFrame()
        waiter = asyncio.create_task(frames.next())
        await asyncio.sleep(0)
        assert not waiter.done()

        frames.publish(packet(1))

        assert (await waiter).sequence == 1
        assert frames.latest == packet(1)

    async def test_next_after_sequence_waits_for_a_newer_frame(self) -> None:
        frames = LatestFrame()
        frames.publish(packet(1))
        waiter = asyncio.create_task(frames.next(after_sequence=1))
        await asyncio.sleep(0)
        assert not waiter.done()

        frames.publish(packet(2))

        assert (await waiter).sequence == 2

    async def test_one_publish_wakes_every_consumer(self) -> None:
        frames = LatestFrame()
        waiters = [asyncio.create_task(frames.next()) for _ in range(5)]
        await asyncio.sleep(0)

        frames.publish(packet(7))

        assert [w.sequence for w in await asyncio.gather(*waiters)] == [7] * 5

    async def test_viewers_are_counted_while_watching(self) -> None:
        frames = LatestFrame()

        async with frames.watching(), frames.watching():
            assert frames.viewers == 2
        assert frames.viewers == 0


class TestLoopBridge:
    async def test_frames_from_a_thread_are_coalesced_to_the_latest(self) -> None:
        delivered: list[int] = []

        class RecordingFrames(LatestFrame):
            def publish(self, packet: FramePacket) -> None:
                delivered.append(packet.sequence)
                super().publish(packet)

        frames = RecordingFrames()
        bridge = LoopBridge(
            asyncio.get_running_loop(),
            frames=frames,
            on_event=lambda _e: None,
            on_exit=lambda _c: None,
        )

        def flood() -> None:
            for sequence in range(1, 501):
                bridge.publish_frame(packet(sequence))

        thread = threading.Thread(target=flood)
        thread.start()
        thread.join()
        await asyncio.sleep(0.05)

        assert frames.latest is not None
        assert frames.latest.sequence == 500
        assert len(delivered) < 500  # the loop never processed a 500-frame backlog
        assert delivered == sorted(delivered)

    async def test_events_and_exit_are_all_delivered_in_order(self) -> None:
        events: list[CameraEvent] = []
        exits: list[bool] = []
        bridge = LoopBridge(
            asyncio.get_running_loop(),
            frames=LatestFrame(),
            on_event=events.append,
            on_exit=exits.append,
        )
        statuses = [DeviceStatus.STARTING, DeviceStatus.ONLINE, DeviceStatus.STOPPED]

        def worker() -> None:
            for status in statuses:
                bridge.publish_event(DeviceStatusChanged(device_id="cam", status=status, at=T0))
            bridge.worker_exited(crashed=False)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        await asyncio.sleep(0.01)

        assert [e.status for e in events if isinstance(e, DeviceStatusChanged)] == statuses
        assert exits == [False]

    async def test_viewer_flag_reflects_watchers(self) -> None:
        frames = LatestFrame()
        bridge = LoopBridge(
            asyncio.get_running_loop(),
            frames=frames,
            on_event=lambda _e: None,
            on_exit=lambda _c: None,
        )

        assert bridge.has_viewers is False
        async with frames.watching():
            assert bridge.has_viewers is True


def test_publishing_after_the_loop_closed_is_harmless() -> None:
    loop = asyncio.new_event_loop()
    bridge = LoopBridge(
        loop, frames=LatestFrame(), on_event=lambda _e: None, on_exit=lambda _c: None
    )
    loop.close()

    bridge.publish_frame(packet(1))
    bridge.publish_event(DeviceStatusChanged(device_id="cam", status=DeviceStatus.STOPPED, at=T0))
    bridge.worker_exited(crashed=False)
