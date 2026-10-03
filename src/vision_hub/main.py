"""Application factory.

Run with ``vision-hub`` or ``uvicorn --factory vision_hub.main:create_app``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from vision_hub import __version__
from vision_hub.api.middleware import RequestContextMiddleware
from vision_hub.core.config import Settings, get_settings
from vision_hub.core.container import LifespanState, build_container
from vision_hub.core.errors import register_exception_handlers
from vision_hub.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[LifespanState]:
        logger.info("startup", app=settings.app.name, version=__version__, env=settings.app.env)
        async with build_container(settings) as container:
            yield {"container": container}
        logger.info("shutdown")

    app = FastAPI(
        title=settings.app.name,
        version=__version__,
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    # Added last so it is the outermost middleware and sees every request and error.
    app.add_middleware(RequestContextMiddleware)
    return app
