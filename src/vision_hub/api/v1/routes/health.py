"""Probes for Docker/Kubernetes and load balancers. Public by design: they expose no details."""

from fastapi import APIRouter, Depends, Response, status

from vision_hub.api.deps import HealthChecksDep, SettingsDep
from vision_hub.schemas.health import CheckStatus, Liveness, Readiness
from vision_hub.services.health import run_health_checks


async def _no_store(response: Response) -> None:
    """Probe results must never be served from a cache."""
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/health", tags=["health"], dependencies=[Depends(_no_store)])


@router.get("/live", summary="Liveness probe")
async def live() -> Liveness:
    """The process is up and serving requests. Never checks dependencies, so a database outage
    does not make an orchestrator restart a healthy process."""
    return Liveness()


@router.get(
    "/ready",
    summary="Readiness probe",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
)
async def ready(response: Response, checks: HealthChecksDep, settings: SettingsDep) -> Readiness:
    """All dependencies are reachable, so the hub can take traffic. Returns 503 otherwise."""
    report = await run_health_checks(
        checks, check_timeout=settings.app.health_check_timeout_seconds
    )
    if not report.healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(
        status="ok" if report.healthy else "unavailable",
        checks=[CheckStatus.model_validate(result) for result in report.checks],
    )
