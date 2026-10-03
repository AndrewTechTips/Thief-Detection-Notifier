"""Service container: long-lived objects created at startup and released at shutdown."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TypedDict

from vision_hub.core.config import Settings
from vision_hub.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class Container:
    """Typed registry of application services, resolved in routes via ``ContainerDep``.

    New services (event bus, camera manager, DB engine, ...) are added as fields and wired in
    ``build_container``; nothing is stored in module-level globals.
    """

    settings: Settings


class LifespanState(TypedDict):
    """Starlette copies this into ``request.state`` for every request and WebSocket."""

    container: Container


@asynccontextmanager
async def build_container(settings: Settings) -> AsyncIterator[Container]:
    """Create services on startup and release them, in reverse order, on shutdown."""
    container = Container(settings=settings)
    logger.info("container_started")
    try:
        yield container
    finally:
        logger.info("container_stopped")
