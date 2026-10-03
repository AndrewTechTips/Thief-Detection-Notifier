"""In-memory auth adapters for the single-node MVP. Phase 3 replaces both with database-backed
implementations of the same ports, so revocations then survive restarts."""

from collections.abc import Callable
from datetime import datetime

from vision_hub.core.config import SecurityConfig
from vision_hub.core.security import utc_now
from vision_hub.domain.auth import Role, User


class SettingsUserRepository:
    """Exposes the single admin account bootstrapped from configuration."""

    def __init__(self, config: SecurityConfig) -> None:
        hash_ = config.admin_password_hash
        self._admin = (
            User(
                username=config.admin_username,
                role=Role.ADMIN,
                password_hash=hash_.get_secret_value(),
            )
            if hash_ is not None
            else None
        )

    @property
    def has_users(self) -> bool:
        return self._admin is not None

    async def get_by_username(self, username: str) -> User | None:
        if self._admin is not None and username == self._admin.username:
            return self._admin
        return None


class InMemoryTokenRevocationStore:
    def __init__(self, clock: Callable[[], datetime] = utc_now) -> None:
        self._revoked: dict[str, datetime] = {}
        self._clock = clock

    def revoke(self, token_id: str, expires_at: datetime) -> None:
        self._purge_expired()
        self._revoked[token_id] = expires_at

    def is_revoked(self, token_id: str) -> bool:
        return token_id in self._revoked

    def __len__(self) -> int:
        return len(self._revoked)

    def _purge_expired(self) -> None:
        """Expired tokens are rejected by signature checks anyway; forget them to bound memory."""
        now = self._clock()
        for token_id in [t for t, expires_at in self._revoked.items() if expires_at <= now]:
            del self._revoked[token_id]
