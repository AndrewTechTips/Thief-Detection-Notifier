"""In-memory auth adapters for the single-node MVP. Phase 3 replaces both with database-backed
implementations of the same ports, so revocations then survive restarts."""

import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

from vision_hub.core.config import SecurityConfig
from vision_hub.core.security import utc_now
from vision_hub.domain.auth import Principal, Role, User


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


class InMemoryTicketStore:
    """Short-lived, single-use tickets that stand in for a bearer token where browsers cannot
    send headers (WebSockets, ``<img>`` MJPEG streams). A leaked ticket is useless once used or
    after ``ttl``; tickets never appear in our logs because query strings are not logged."""

    def __init__(self, ttl_seconds: float, clock: Callable[[], datetime] = utc_now) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._tickets: dict[str, tuple[Principal, datetime]] = {}

    @property
    def ttl_seconds(self) -> int:
        return int(self._ttl.total_seconds())

    def issue(self, principal: Principal) -> str:
        now = self._clock()
        for ticket in [t for t, (_, expires) in self._tickets.items() if expires <= now]:
            del self._tickets[ticket]
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = (principal, now + self._ttl)
        return ticket

    def consume(self, ticket: str) -> Principal | None:
        entry = self._tickets.pop(ticket, None)
        if entry is None:
            return None
        principal, expires = entry
        return principal if expires > self._clock() else None

    def __len__(self) -> int:
        return len(self._tickets)
