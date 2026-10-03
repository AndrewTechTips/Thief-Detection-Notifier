"""Cryptographic primitives: password hashing (Argon2id) and signed tokens (JWT)."""

import asyncio
import base64
import hashlib
import hmac
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import cached_property

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pydantic import SecretStr

from vision_hub.core.config import SecurityConfig
from vision_hub.core.errors import AuthenticationError
from vision_hub.domain.auth import Principal, Role

type Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


class PasswordHasher:
    """Argon2id hashing. Verification runs in a worker thread: it is deliberately CPU-heavy
    (~30 ms) and would otherwise stall the event loop and every camera stream with it."""

    def __init__(self, hasher: PasswordHash | None = None) -> None:
        self._hasher = hasher or PasswordHash.recommended()

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    async def verify(self, password: str, password_hash: str | None) -> bool:
        """``password_hash=None`` (unknown user) still costs a full verification, so response
        time does not reveal whether a username exists."""
        return await asyncio.to_thread(self._verify, password, password_hash)

    @cached_property
    def _dummy_hash(self) -> str:
        return self._hasher.hash(secrets.token_urlsafe(32))

    def _verify(self, password: str, password_hash: str | None) -> bool:
        if password_hash is None:
            self._hasher.verify(password, self._dummy_hash)
            return False
        try:
            return self._hasher.verify(password, password_hash)
        except UnknownHashError:
            return False


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    principal: Principal
    token_type: TokenType
    token_id: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    claims: TokenClaims
    expires_in: int  # seconds, as OAuth2 token responses report it


_REQUIRED_CLAIMS = ["sub", "role", "typ", "jti", "iat", "exp", "iss", "aud"]


class TokenService:
    """Issues and verifies HMAC-signed JWTs. The algorithm is pinned on decode, so tokens
    signed with ``none`` or a different algorithm are rejected."""

    def __init__(self, config: SecurityConfig, clock: Clock = utc_now) -> None:
        self._config = config
        self._clock = clock

    def issue(self, principal: Principal, token_type: TokenType) -> IssuedToken:
        issued_at = self._clock()
        lifetime = (
            timedelta(minutes=self._config.access_token_ttl_minutes)
            if token_type is TokenType.ACCESS
            else timedelta(days=self._config.refresh_token_ttl_days)
        )
        claims = TokenClaims(
            principal=principal,
            token_type=token_type,
            token_id=str(uuid.uuid7()),
            expires_at=issued_at + lifetime,
        )
        payload = {
            "sub": principal.username,
            "role": principal.role.value,
            "typ": token_type.value,
            "jti": claims.token_id,
            "iat": issued_at,
            "exp": claims.expires_at,
            "iss": self._config.jwt_issuer,
            "aud": self._config.jwt_audience,
        }
        token = jwt.encode(
            payload,
            self._config.jwt_secret.get_secret_value(),
            algorithm=self._config.jwt_algorithm,
        )
        return IssuedToken(token=token, claims=claims, expires_in=int(lifetime.total_seconds()))

    def decode(self, token: str, expected_type: TokenType) -> TokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._config.jwt_secret.get_secret_value(),
                algorithms=[self._config.jwt_algorithm],
                audience=self._config.jwt_audience,
                issuer=self._config.jwt_issuer,
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.ExpiredSignatureError:
            raise AuthenticationError("Token has expired.") from None
        except jwt.InvalidTokenError:
            raise AuthenticationError("Token is invalid.") from None

        # A refresh token must never work as an access token, and vice versa.
        if payload["typ"] != expected_type.value:
            raise AuthenticationError("Token is invalid.")
        try:
            role = Role(payload["role"])
        except ValueError:
            raise AuthenticationError("Token is invalid.") from None
        return TokenClaims(
            principal=Principal(username=payload["sub"], role=role),
            token_type=expected_type,
            token_id=payload["jti"],
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )


class UrlSigner:
    """HMAC-signed, expiring links (like S3 presigned URLs) for resources browsers load
    without headers, such as event thumbnails in ``<img>`` tags.

    The key is derived from the JWT secret with a distinct label, so a signature can never be
    confused with a token signature.
    """

    def __init__(
        self,
        secret: SecretStr,
        *,
        ttl_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._key = hmac.new(
            secret.get_secret_value().encode(), b"vision-hub/signed-urls", hashlib.sha256
        ).digest()
        self._ttl = ttl_seconds
        self._clock = clock

    def sign(self, resource: str) -> tuple[int, str]:
        """Return ``(expires, signature)`` for ``resource`` (path plus any bound parameters)."""
        expires = int(self._clock()) + self._ttl
        return expires, self._signature(resource, expires)

    def verify(self, resource: str, expires: int, signature: str) -> bool:
        if expires < self._clock():
            return False
        return hmac.compare_digest(signature, self._signature(resource, expires))

    def _signature(self, resource: str, expires: int) -> str:
        digest = hmac.new(self._key, f"{resource}\n{expires}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
