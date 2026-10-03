"""Authentication: OAuth2 password login, refresh-token rotation, logout and identity."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm

from vision_hub.api.deps import AuthServiceDep, ContainerDep, CurrentPrincipal
from vision_hub.schemas.auth import PrincipalOut, RefreshRequest, TokenResponse
from vision_hub.schemas.problem import ProblemDetail
from vision_hub.services.auth import TokenPair


async def _no_store(response: Response) -> None:
    """Token responses must not be cached (RFC 6749 §5.1)."""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


async def _rate_limit(request: Request, container: ContainerDep) -> None:
    client = request.client.host if request.client else "unknown"
    await container.auth_rate_limiter.hit(f"{request.url.path}:{client}")


def _token_response(pair: TokenPair) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access.token,
        expires_in=pair.access.expires_in,
        refresh_token=pair.refresh.token,
        refresh_expires_in=pair.refresh.expires_in,
    )


type _Responses = dict[int | str, dict[str, Any]]
_UNAUTHORIZED: _Responses = {status.HTTP_401_UNAUTHORIZED: {"model": ProblemDetail}}
_RATE_LIMITED: _Responses = {status.HTTP_429_TOO_MANY_REQUESTS: {"model": ProblemDetail}}

# Public: these endpoints are how a client obtains credentials in the first place.
router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(_no_store)])


@router.post(
    "/token",
    summary="Log in",
    dependencies=[Depends(_rate_limit)],
    responses={**_UNAUTHORIZED, **_RATE_LIMITED},
)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], auth: AuthServiceDep
) -> TokenResponse:
    """OAuth2 password flow (form-encoded `username` and `password`). Rate-limited per client."""
    return _token_response(await auth.login(form.username, form.password))


@router.post(
    "/refresh",
    summary="Refresh tokens",
    dependencies=[Depends(_rate_limit)],
    responses={**_UNAUTHORIZED, **_RATE_LIMITED},
)
async def refresh(body: RefreshRequest, auth: AuthServiceDep) -> TokenResponse:
    """Exchange a refresh token for a new token pair. The old refresh token stops working."""
    return _token_response(await auth.refresh(body.refresh_token))


@router.post(
    "/logout",
    summary="Log out",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_UNAUTHORIZED,
)
async def logout(body: RefreshRequest, auth: AuthServiceDep) -> None:
    """Revoke the refresh token. The current access token expires on its own within minutes."""
    await auth.logout(body.refresh_token)


# Mounted on the protected router.
identity_router = APIRouter(prefix="/auth", tags=["auth"])


@identity_router.get("/me", summary="Current user")
async def me(principal: CurrentPrincipal) -> PrincipalOut:
    return PrincipalOut.model_validate(principal)
