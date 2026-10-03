from fastapi import APIRouter

from vision_hub.api.v1.routes import health

API_V1_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_V1_PREFIX)
api_router.include_router(health.router)

# Polled every few seconds by orchestrators; logged at DEBUG unless they fail.
QUIET_PATHS = frozenset({f"{API_V1_PREFIX}/health/live", f"{API_V1_PREFIX}/health/ready"})
