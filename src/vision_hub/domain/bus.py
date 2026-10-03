"""Publish/subscribe port connecting event producers (cameras) to consumers (notifications,
WebSocket clients). The in-memory adapter serves the single-node MVP (AD-9); a Redis adapter can
implement the same interface later."""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, Protocol

type Overflow = Literal["drop_oldest", "drop_newest"]


@dataclass(frozen=True, slots=True)
class Envelope[T]:
    topic: str
    message: T


class Subscription[T](AbstractAsyncContextManager["Subscription[T]"], Protocol):
    """Async-iterate to receive messages; leaving the ``async with`` block unsubscribes."""

    @property
    def dropped(self) -> int:
        """Messages discarded because this subscriber fell behind."""
        ...

    def __aiter__(self) -> AsyncIterator[Envelope[T]]: ...

    def close(self) -> None:
        """Stop receiving; iteration ends after messages already queued are consumed."""
        ...


class EventBus[T](Protocol):
    def publish(self, topic: str, message: T) -> None:
        """Deliver to every matching subscriber. Never blocks: adapters buffer if needed."""
        ...

    def subscribe(
        self, *patterns: str, maxsize: int = 0, overflow: Overflow = "drop_oldest"
    ) -> Subscription[T]:
        """Receive messages whose topic matches any glob ``pattern`` (e.g. ``motion.ended.*``).

        ``maxsize=0`` means unbounded (internal services that must not miss events); bounded
        subscribers (live clients) lose messages per ``overflow`` when they fall behind.
        """
        ...
