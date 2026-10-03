"""Shutdown signal shared by the server and the application.

Uvicorn waits for open connections to finish *before* it runs the lifespan shutdown, so
endless responses (MJPEG streams) would hold shutdown up forever. ``vision-hub serve`` calls
``begin_shutdown`` as soon as the server stops accepting connections; listeners end those
responses and the readiness probe starts failing, so load balancers stop routing traffic.
"""

from collections.abc import Callable

from vision_hub.core.logging import get_logger
from vision_hub.domain.health import Unhealthy

logger = get_logger(__name__)


class Lifecycle:
    def __init__(self) -> None:
        self._stopping = False
        self._listeners: list[Callable[[], None]] = []

    @property
    def stopping(self) -> bool:
        return self._stopping

    def on_shutdown(self, listener: Callable[[], None]) -> None:
        """Call ``listener`` once shutdown begins (immediately if it already has)."""
        if self._stopping:
            listener()
        else:
            self._listeners.append(listener)

    def begin_shutdown(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        logger.info("shutdown_started")
        listeners, self._listeners = self._listeners, []
        for listener in listeners:
            try:
                listener()
            except Exception:
                logger.exception("shutdown_listener_failed")


class ShutdownCheck:
    """Readiness fails while the hub is shutting down."""

    name = "accepting_traffic"

    def __init__(self, lifecycle: Lifecycle) -> None:
        self._lifecycle = lifecycle

    async def check(self) -> None:
        if self._lifecycle.stopping:
            msg = "shutting down"
            raise Unhealthy(msg)
