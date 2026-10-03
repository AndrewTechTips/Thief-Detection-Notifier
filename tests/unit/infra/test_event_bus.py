import asyncio

import pytest

from vision_hub.domain.bus import Envelope
from vision_hub.infra.bus.memory import InMemoryEventBus


async def drain(subscription: object, count: int) -> list[Envelope[str]]:
    received = []
    async with asyncio.timeout(1):
        async for envelope in subscription:  # type: ignore[attr-defined]
            received.append(envelope)
            if len(received) == count:
                break
    return received


class TestRouting:
    async def test_exact_and_glob_patterns(self) -> None:
        bus: InMemoryEventBus[str] = InMemoryEventBus()
        porch_only = bus.subscribe("motion.ended.porch")
        all_motion = bus.subscribe("motion.*")

        bus.publish("motion.started.porch", "a")
        bus.publish("motion.ended.porch", "b")
        bus.publish("motion.ended.gate", "c")
        bus.publish("device.status.porch", "ignored")

        assert [e.message for e in await drain(porch_only, 1)] == ["b"]
        assert [e.message for e in await drain(all_motion, 3)] == ["a", "b", "c"]

    async def test_any_of_several_patterns(self) -> None:
        bus: InMemoryEventBus[str] = InMemoryEventBus()
        subscription = bus.subscribe("*.porch", "device.status.*")

        bus.publish("motion.ended.porch", "a")
        bus.publish("device.status.gate", "b")
        bus.publish("motion.ended.gate", "ignored")

        assert [(e.topic, e.message) for e in await drain(subscription, 2)] == [
            ("motion.ended.porch", "a"),
            ("device.status.gate", "b"),
        ]

    async def test_every_subscriber_gets_its_own_copy(self) -> None:
        bus: InMemoryEventBus[str] = InMemoryEventBus()
        first, second = bus.subscribe("t"), bus.subscribe("t")

        bus.publish("t", "x")

        assert (await drain(first, 1))[0].message == (await drain(second, 1))[0].message == "x"

    def test_publishing_without_subscribers_is_fine(self) -> None:
        InMemoryEventBus[str]().publish("nobody.listens", "x")

    def test_a_pattern_is_required(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            InMemoryEventBus[str]().subscribe()


class TestLifecycle:
    async def test_leaving_the_block_unsubscribes(self) -> None:
        bus: InMemoryEventBus[str] = InMemoryEventBus()

        async with bus.subscribe("t"):
            assert bus.subscriber_count == 1
        assert bus.subscriber_count == 0

    async def test_unsubscribing_twice_is_harmless(self) -> None:
        bus: InMemoryEventBus[str] = InMemoryEventBus()

        async with bus.subscribe("t"):
            bus.close()  # shutdown while a consumer is still inside its block

        assert bus.subscriber_count == 0

    async def test_close_ends_iteration_after_queued_messages(self) -> None:
        bus: InMemoryEventBus[str] = InMemoryEventBus()
        subscription = bus.subscribe("t")
        bus.publish("t", "last words")

        bus.close()

        assert [e.message async for e in subscription] == ["last words"]
        assert bus.subscriber_count == 0


class TestBackPressure:
    async def test_drop_oldest_keeps_the_newest(self) -> None:
        bus: InMemoryEventBus[int] = InMemoryEventBus()
        subscription = bus.subscribe("n", maxsize=3, overflow="drop_oldest")

        for n in range(10):
            bus.publish("n", n)
        bus.close()

        assert [e.message async for e in subscription] == [7, 8, 9]
        assert subscription.dropped == 7

    async def test_drop_newest_keeps_the_oldest(self) -> None:
        bus: InMemoryEventBus[int] = InMemoryEventBus()
        subscription = bus.subscribe("n", maxsize=3, overflow="drop_newest")

        for n in range(10):
            bus.publish("n", n)
        bus.close()

        assert [e.message async for e in subscription] == [0, 1, 2]
        assert subscription.dropped == 7

    async def test_unbounded_subscribers_never_drop(self) -> None:
        bus: InMemoryEventBus[int] = InMemoryEventBus()
        subscription = bus.subscribe("n")

        for n in range(1000):
            bus.publish("n", n)
        bus.close()

        assert len([e async for e in subscription]) == 1000
        assert subscription.dropped == 0

    async def test_one_slow_subscriber_does_not_affect_others(self) -> None:
        bus: InMemoryEventBus[int] = InMemoryEventBus()
        slow = bus.subscribe("n", maxsize=1)
        healthy = bus.subscribe("n")

        for n in range(5):
            bus.publish("n", n)
        bus.close()

        assert len([e async for e in healthy]) == 5
        assert slow.dropped == 4
