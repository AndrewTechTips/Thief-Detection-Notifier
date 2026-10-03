"""Serves WebSocket clients: forwards camera events, handles subscriptions and heartbeats.

Each connection runs three tasks: a forwarder (bus -> client), a reader (client messages) and
a heartbeat; whichever ends first decides how the connection closes. Writes go through one
lock so they never interleave. A client that falls behind is disconnected instead of silently
missing alerts.
"""

import asyncio
import contextlib
import json
import time
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import ValidationError
from starlette.types import Message

from vision_hub.core.config import RealtimeConfig
from vision_hub.core.logging import get_logger
from vision_hub.domain.auth import Principal
from vision_hub.domain.bus import EventBus, Subscription
from vision_hub.domain.events import (
    DEVICE_STATUS,
    MOTION_ENDED,
    MOTION_STARTED,
    CameraEvent,
    MotionEndedEvent,
    MotionStartedEvent,
)
from vision_hub.schemas.ws import (
    ErrorData,
    ErrorMessage,
    PingMessage,
    ReplayDoneData,
    ReplayDoneMessage,
    ResumeMessage,
    ServerMessage,
    SubscribeMessage,
    SubscriptionData,
    SubscriptionMessage,
    UnsubscribeMessage,
    client_message_adapter,
    replayed_message,
    to_message,
)
from vision_hub.services.events import EventService

REPLAY_LIMIT = 100

logger = get_logger(__name__)


class CloseCode:
    NORMAL = 1000
    GOING_AWAY = 1001  # server shutting down
    INTERNAL_ERROR = 1011
    TRY_AGAIN_LATER = 1013  # client too slow to keep up
    UNAUTHORIZED = 4401
    IDLE_TIMEOUT = 4408


class ClientSocket(Protocol):
    """The subset of ``starlette.websockets.WebSocket`` used here."""

    async def send_text(self, data: str) -> None: ...

    async def receive(self) -> Message: ...

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


class _ClosedError(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass
class _Filter:
    all_devices: bool = True
    devices: set[str] = field(default_factory=set)  # included, or excluded when all_devices

    def matches(self, device_id: str) -> bool:
        return (device_id not in self.devices) if self.all_devices else device_id in self.devices

    def describe(self) -> SubscriptionData:
        if self.all_devices:
            return SubscriptionData(devices=None, excluded=sorted(self.devices))
        return SubscriptionData(devices=sorted(self.devices), excluded=[])


class _Connection:
    def __init__(
        self,
        socket: ClientSocket,
        principal: Principal,
        subscription: Subscription[CameraEvent],
        config: RealtimeConfig,
        history: EventService | None,
    ) -> None:
        self.history = history
        self.socket = socket
        self.principal = principal
        self.subscription = subscription
        self.config = config
        self.filter = _Filter()
        self.last_seen = time.monotonic()
        self._send_lock = asyncio.Lock()

    async def send(self, message: ServerMessage) -> None:
        payload = message.model_dump_json()
        async with self._send_lock:
            try:
                async with asyncio.timeout(self.config.send_timeout_seconds):
                    await self.socket.send_text(payload)
            except TimeoutError:
                raise _ClosedError(CloseCode.TRY_AGAIN_LATER, "send timed out") from None

    async def forward(self) -> None:
        async for envelope in self.subscription:
            if self.subscription.dropped:
                raise _ClosedError(CloseCode.TRY_AGAIN_LATER, "client too slow")
            event = envelope.message
            device_id = (
                event.event.device_id
                if isinstance(event, MotionStartedEvent | MotionEndedEvent)
                else event.device_id
            )
            if self.filter.matches(device_id):
                await self.send(to_message(event))
        raise _ClosedError(CloseCode.GOING_AWAY, "server shutting down")

    async def read(self) -> None:
        while True:
            message = await self.socket.receive()
            if message["type"] == "websocket.disconnect":
                raise _ClosedError(int(message.get("code", CloseCode.NORMAL)), "client left")
            self.last_seen = time.monotonic()
            await self._handle(message.get("text"))

    async def heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.config.ping_interval_seconds)
            if time.monotonic() - self.last_seen > self.config.idle_timeout_seconds:
                raise _ClosedError(CloseCode.IDLE_TIMEOUT, "idle timeout")
            await self.send(PingMessage())

    async def _handle(self, text: str | None) -> None:
        if text is None:
            await self.send(_error("unsupported", "Only JSON text messages are supported."))
            return
        try:
            parsed = client_message_adapter.validate_json(text)
        except ValidationError as exc:
            # json.loads only to tell broken JSON from a well-formed but unknown message
            code = "invalid_json" if not _is_json(text) else "invalid_message"
            await self.send(_error(code, exc.errors(include_input=False)[0]["msg"]))
            return
        match parsed:
            case ResumeMessage(after=after):
                await self._replay(str(after))
                return
            case SubscribeMessage(devices=None):
                self.filter = _Filter()
            case SubscribeMessage(devices=devices):
                self.filter = _Filter(all_devices=False, devices=set(devices or ()))
            case UnsubscribeMessage(devices=devices):
                if self.filter.all_devices:
                    self.filter.devices |= set(devices)
                else:
                    self.filter.devices -= set(devices)
            case _:  # pong: last_seen already updated
                return
        await self.send(SubscriptionMessage(data=self.filter.describe()))

    async def _replay(self, after: str) -> None:
        if self.history is None:
            await self.send(_error("unsupported", "Event history is not available."))
            return
        records = await self.history.after(after, limit=REPLAY_LIMIT + 1)
        replayed = 0
        for record in records[:REPLAY_LIMIT]:
            if self.filter.matches(record.event.device_id):
                await self.send(replayed_message(record))
                replayed += 1
        truncated = len(records) > REPLAY_LIMIT
        await self.send(ReplayDoneMessage(data=ReplayDoneData(count=replayed, truncated=truncated)))


class ConnectionManager:
    def __init__(
        self,
        bus: EventBus[CameraEvent],
        config: RealtimeConfig,
        history: EventService | None = None,
    ) -> None:
        self._bus = bus
        self._config = config
        self._history = history
        self._connections: set[_Connection] = set()

    @property
    def active(self) -> int:
        return len(self._connections)

    async def serve(self, socket: ClientSocket, principal: Principal) -> None:
        """Run an accepted connection until either side closes it."""
        subscription = self._bus.subscribe(
            f"{MOTION_STARTED}.*",
            f"{MOTION_ENDED}.*",
            f"{DEVICE_STATUS}.*",
            maxsize=self._config.client_queue_size,
            overflow="drop_newest",
        )
        connection = _Connection(socket, principal, subscription, self._config, self._history)
        self._connections.add(connection)
        log = logger.bind(user=principal.username)
        log.info("ws_connected", clients=self.active)
        code, reason = CloseCode.NORMAL, "closed"
        try:
            async with subscription:
                await connection.send(SubscriptionMessage(data=connection.filter.describe()))
                code, reason = await _run_until_first_exit(
                    connection.forward(), connection.read(), connection.heartbeat()
                )
        except _ClosedError as closed:  # the initial send failed
            code, reason = closed.code, closed.reason
        finally:
            self._connections.discard(connection)
            if reason != "client left":
                await _close_quietly(socket, code, reason)
            log.info("ws_disconnected", code=code, reason=reason, clients=self.active)

    def close_all(self) -> None:
        """Shutdown: ending each subscription makes its forwarder close with 1001."""
        for connection in tuple(self._connections):
            connection.subscription.close()


async def _run_until_first_exit(*coroutines: Coroutine[Any, Any, None]) -> tuple[int, str]:
    """Run the connection's tasks until one ends, then cancel the rest.

    ``asyncio.wait`` rather than a ``TaskGroup``: a failing TaskGroup child cancels the parent
    task, which anyio-managed hosts (e.g. Starlette's TestClient) misread as an outside
    cancellation.
    """
    tasks = [asyncio.create_task(coroutine) for coroutine in coroutines]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    error = next(task.exception() for task in done)
    if isinstance(error, _ClosedError):
        return error.code, error.reason
    logger.error("ws_connection_failed", exc_info=error)
    return CloseCode.INTERNAL_ERROR, "internal error"


def _error(code: str, message: str) -> ErrorMessage:
    return ErrorMessage(data=ErrorData(code=code, message=message))


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


async def _close_quietly(socket: ClientSocket, code: int, reason: str) -> None:
    with contextlib.suppress(RuntimeError):  # already closed by the peer
        await socket.close(code=code, reason=reason)
