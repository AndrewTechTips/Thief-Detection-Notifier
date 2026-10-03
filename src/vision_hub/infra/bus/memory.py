"""Asyncio fan-out event bus. Loop thread only (cameras publish via ``LoopBridge``)."""

import asyncio
from collections.abc import AsyncIterator
from fnmatch import fnmatchcase
from types import TracebackType

from vision_hub.core.logging import get_logger
from vision_hub.domain.bus import Envelope, Overflow

logger = get_logger(__name__)


class InMemorySubscription[T]:
    def __init__(
        self, bus: InMemoryEventBus[T], patterns: tuple[str, ...], maxsize: int, overflow: Overflow
    ) -> None:
        if not patterns:
            msg = "subscribe() needs at least one topic pattern"
            raise ValueError(msg)
        self._bus = bus
        self._patterns = patterns
        self._overflow = overflow
        self._queue: asyncio.Queue[Envelope[T]] = asyncio.Queue(maxsize)
        self._dropped = 0

    @property
    def dropped(self) -> int:
        return self._dropped

    def matches(self, topic: str) -> bool:
        return any(fnmatchcase(topic, pattern) for pattern in self._patterns)

    def offer(self, envelope: Envelope[T]) -> None:
        if self._queue.full():
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 100 == 0:
                logger.warning(
                    "bus_subscriber_lagging",
                    patterns=self._patterns,
                    dropped=self._dropped,
                    policy=self._overflow,
                )
            if self._overflow == "drop_newest":
                return
            self._queue.get_nowait()
        self._queue.put_nowait(envelope)

    def close(self) -> None:
        """Ends iteration once already-queued messages are consumed."""
        self._queue.shutdown()

    async def __aenter__(self) -> InMemorySubscription[T]:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._bus.unsubscribe(self)

    def __aiter__(self) -> AsyncIterator[Envelope[T]]:
        return self

    async def __anext__(self) -> Envelope[T]:
        try:
            return await self._queue.get()
        except asyncio.QueueShutDown:
            raise StopAsyncIteration from None


class InMemoryEventBus[T]:
    def __init__(self) -> None:
        self._subscriptions: list[InMemorySubscription[T]] = []

    @property
    def subscriber_count(self) -> int:
        return len(self._subscriptions)

    def publish(self, topic: str, message: T) -> None:
        envelope = Envelope(topic=topic, message=message)
        for subscription in tuple(self._subscriptions):
            if subscription.matches(topic):
                subscription.offer(envelope)

    def subscribe(
        self, *patterns: str, maxsize: int = 0, overflow: Overflow = "drop_oldest"
    ) -> InMemorySubscription[T]:
        """Registers immediately, so nothing published after this call is missed."""
        subscription = InMemorySubscription(self, patterns, maxsize, overflow)
        self._subscriptions.append(subscription)
        return subscription

    def unsubscribe(self, subscription: InMemorySubscription[T]) -> None:
        if subscription in self._subscriptions:
            self._subscriptions.remove(subscription)
        subscription.close()

    def close(self) -> None:
        """Shutdown: end every subscriber's iteration."""
        for subscription in tuple(self._subscriptions):
            self.unsubscribe(subscription)
