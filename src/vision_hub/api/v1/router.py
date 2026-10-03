"""Route composition. Everything is protected by default: a new router goes into
``protected_router`` unless it has a reason to be public, and then it must be added to
``PUBLIC_PATHS`` (a test enforces this)."""

from fastapi import APIRouter, Depends, status

from vision_hub.api.deps import get_current_principal
from vision_hub.api.v1 import API_V1_PREFIX
from vision_hub.api.v1.routes import auth, health
from vision_hub.schemas.problem import ProblemDetail

public_router = APIRouter()
public_router.include_router(health.router)
public_router.include_router(auth.router)

protected_router = APIRouter(
    dependencies=[Depends(get_current_principal)],
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ProblemDetail}},
)
protected_router.include_router(auth.identity_router)

api_router = APIRouter(prefix=API_V1_PREFIX)
api_router.include_router(public_router)
api_router.include_router(protected_router)

PUBLIC_PATHS = frozenset(
    {
        f"{API_V1_PREFIX}/health/live",
        f"{API_V1_PREFIX}/health/ready",
        f"{API_V1_PREFIX}/auth/token",
        f"{API_V1_PREFIX}/auth/refresh",
        f"{API_V1_PREFIX}/auth/logout",
    }
)

# Polled every few seconds by orchestrators; logged at DEBUG unless they fail.
QUIET_PATHS = frozenset({f"{API_V1_PREFIX}/health/live", f"{API_V1_PREFIX}/health/ready"})
