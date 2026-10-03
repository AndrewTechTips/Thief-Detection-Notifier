"""Login, token refresh with rotation, and logout."""

from dataclasses import dataclass

from vision_hub.core.errors import AuthenticationError
from vision_hub.core.logging import get_logger
from vision_hub.core.security import IssuedToken, PasswordHasher, TokenService, TokenType
from vision_hub.domain.auth import Principal, TokenRevocationStore, UserRepository

logger = get_logger(__name__)

INVALID_CREDENTIALS = "Incorrect username or password."


@dataclass(frozen=True, slots=True)
class TokenPair:
    access: IssuedToken
    refresh: IssuedToken


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        hasher: PasswordHasher,
        tokens: TokenService,
        revocations: TokenRevocationStore,
    ) -> None:
        self._users = users
        self._hasher = hasher
        self._tokens = tokens
        self._revocations = revocations

    async def login(self, username: str, password: str) -> TokenPair:
        user = await self._users.get_by_username(username)
        valid = await self._hasher.verify(password, user.password_hash if user else None)
        if user is None or not valid:
            # Same message for unknown user and wrong password: no username enumeration.
            logger.warning("login_failed", username=username)
            raise AuthenticationError(INVALID_CREDENTIALS)
        logger.info("login_succeeded", username=user.username, role=user.role)
        return self._issue_pair(Principal(username=user.username, role=user.role))

    async def refresh(self, refresh_token: str) -> TokenPair:
        """Rotate: the presented refresh token is revoked and a new pair is issued. Presenting
        an already-used token again signals theft, so it is rejected and logged."""
        claims = self._tokens.decode(refresh_token, TokenType.REFRESH)
        # A single atomic revoke: of two concurrent refreshes with one token, only one wins.
        if not await self._revocations.revoke(claims.token_id, claims.expires_at):
            logger.warning("refresh_token_reused", username=claims.principal.username)
            raise AuthenticationError("Refresh token has been revoked.")

        # Re-read the user so removed accounts and role changes take effect at refresh time.
        user = await self._users.get_by_username(claims.principal.username)
        if user is None:
            raise AuthenticationError("Refresh token has been revoked.")
        return self._issue_pair(Principal(username=user.username, role=user.role))

    async def logout(self, refresh_token: str) -> None:
        """Revoke the refresh token. Access tokens stay valid until they expire (minutes)."""
        claims = self._tokens.decode(refresh_token, TokenType.REFRESH)
        await self._revocations.revoke(claims.token_id, claims.expires_at)
        logger.info("logout", username=claims.principal.username)

    def authenticate(self, access_token: str) -> Principal:
        return self._tokens.decode(access_token, TokenType.ACCESS).principal

    def _issue_pair(self, principal: Principal) -> TokenPair:
        return TokenPair(
            access=self._tokens.issue(principal, TokenType.ACCESS),
            refresh=self._tokens.issue(principal, TokenType.REFRESH),
        )
