"""Application factory.

Run with ``vision-hub`` or ``uvicorn --factory vision_hub.main:create_app``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vision_hub import __version__
from vision_hub.api.middleware import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
    TrustedHostMiddleware,
)
from vision_hub.api.openapi import install_problem_details_schema, operation_id
from vision_hub.api.v1.router import PROBE_PATHS, api_router
from vision_hub.core.config import Settings, get_settings
from vision_hub.core.container import LifespanState, build_container
from vision_hub.core.errors import register_exception_handlers
from vision_hub.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

OPENAPI_TAGS = [
    {
        "name": "health",
        "description": "Liveness and readiness probes for orchestrators and load balancers.",
    },
    {
        "name": "auth",
        "description": "Log in with the OAuth2 password flow, rotate refresh tokens, log out.",
    },
    {
        "name": "devices",
        "description": "Cameras: configuration, start/stop, source tests and live snapshots.",
    },
    {
        "name": "events",
        "description": "Recorded motion events with their snapshots.",
    },
]
DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[LifespanState]:
        logger.info("startup", app=settings.app.name, version=__version__, env=settings.app.env)
        async with build_container(settings) as container:
            yield {"container": container}
        logger.info("shutdown")

    docs = settings.docs_enabled
    app = FastAPI(
        title=settings.app.name,
        version=__version__,
        summary="Asynchronous camera monitoring: motion events, alerts and live streams.",
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        generate_unique_id_function=operation_id,
        lifespan=lifespan,
    )
    install_problem_details_schema(app)
    register_exception_handlers(app)
    app.include_router(api_router)
    _add_middleware(app, settings)
    return app


def _add_middleware(app: FastAPI, settings: Settings) -> None:
    """Each call wraps the previous ones, so the request path is the reverse of this order:
    RequestContext -> SecurityHeaders -> TrustedHost -> CORS -> routes."""
    security = settings.security
    app.add_middleware(
        CORSMiddleware,
        allow_origins=security.cors_origins,
        # Bearer tokens travel in the Authorization header, never in cookies.
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
        max_age=600,
    )
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=security.allowed_hosts, exempt_paths=PROBE_PATHS
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_prod, relaxed_paths=DOCS_PATHS)
    # Outermost, so request IDs and access logs cover every response, including rejections.
    app.add_middleware(RequestContextMiddleware, quiet_paths=PROBE_PATHS)
