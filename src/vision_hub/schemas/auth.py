from typing import Literal

from pydantic import Field

from vision_hub.domain.auth import Role
from vision_hub.schemas.base import ApiSchema, RequestSchema


class TokenResponse(ApiSchema):
    """OAuth2 token response (RFC 6749 §5.1), plus the refresh token's lifetime."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_in: int = Field(description="Access token lifetime in seconds")
    refresh_token: str
    refresh_expires_in: int = Field(description="Refresh token lifetime in seconds")


class RefreshRequest(RequestSchema):
    refresh_token: str = Field(min_length=1, max_length=4096)


class PrincipalOut(ApiSchema):
    username: str
    role: Role
