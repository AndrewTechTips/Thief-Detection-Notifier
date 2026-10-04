"""``GET /metrics`` for Prometheus. Outside the versioned API: it is an operations endpoint."""

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from starlette.requests import HTTPConnection

from vision_hub.api.deps import ContainerDep, oauth2_scheme
from vision_hub.core.errors import AuthenticationError, PermissionDeniedError
from vision_hub.core.metrics import CONTENT_TYPE
from vision_hub.domain.auth import Role

METRICS_PATH = "/metrics"


async def require_scraper(
    connection: HTTPConnection,
    container: ContainerDep,
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> None:
    """The configured scrape token, or an admin's access token."""
    if not token:
        raise AuthenticationError("Missing bearer token.")
    expected = container.settings.metrics.token
    if expected is not None and hmac.compare_digest(token, expected.get_secret_value()):
        return
    principal = container.auth.authenticate(token)
    connection.state.username = principal.username
    if not principal.role.includes(Role.ADMIN):
        raise PermissionDeniedError("Metrics require the scrape token or the 'admin' role.")


router = APIRouter(include_in_schema=False)


@router.get(METRICS_PATH, dependencies=[Depends(require_scraper)])
async def metrics(container: ContainerDep) -> Response:
    return Response(
        await container.metrics.render(),
        media_type=CONTENT_TYPE,
        headers={"Cache-Control": "no-store"},
    )
