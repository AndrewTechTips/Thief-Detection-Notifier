"""Route composition. Everything is protected by default: a new router goes into
``protected_router`` unless it has a reason to be public, and then it must be added to
``PUBLIC_PATHS`` (a test enforces this)."""

from fastapi import APIRouter, Depends, status

from vision_hub.api.deps import get_current_principal, get_ticket_or_bearer_principal
from vision_hub.api.v1 import API_V1_PREFIX
from vision_hub.api.v1.routes import auth, devices, health, ws
from vision_hub.schemas.problem import ProblemDetail

public_router = APIRouter()
public_router.include_router(health.router)
public_router.include_router(auth.router)

protected_router = APIRouter(
    dependencies=[Depends(get_current_principal)],
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ProblemDetail}},
)
protected_router.include_router(auth.identity_router)
protected_router.include_router(devices.router)

# Endpoints browsers open without headers accept a single-use ?ticket= as well as a bearer token.
ticket_router = APIRouter(
    dependencies=[Depends(get_ticket_or_bearer_principal)],
    responses={status.HTTP_401_UNAUTHORIZED: {"model": ProblemDetail}},
)
ticket_router.include_router(devices.stream_router)

api_router = APIRouter(prefix=API_V1_PREFIX)
api_router.include_router(public_router)
api_router.include_router(protected_router)
api_router.include_router(ticket_router)
# WebSockets authenticate inside the endpoint (ticket checked before accept).
api_router.include_router(ws.router)

# Dependencies that count as authentication for the secure-by-default test.
AUTH_DEPENDENCIES = (get_current_principal, get_ticket_or_bearer_principal)

PUBLIC_PATHS = frozenset(
    {
        f"{API_V1_PREFIX}/health/live",
        f"{API_V1_PREFIX}/health/ready",
        f"{API_V1_PREFIX}/auth/token",
        f"{API_V1_PREFIX}/auth/refresh",
        f"{API_V1_PREFIX}/auth/logout",
    }
)

# Polled by orchestrators: logged at DEBUG unless they fail, and exempt from the Host check
# (Docker/Kubernetes probes use the container IP as Host).
PROBE_PATHS = frozenset({f"{API_V1_PREFIX}/health/live", f"{API_V1_PREFIX}/health/ready"})
