"""In-memory auth adapters: single-use stream tickets (deliberately short-lived, so memory is
the right place) and a revocation store used in unit tests (production uses the database)."""

import secrets
from collections.abc import Callable
from datetime import datetime, timedelta

from vision_hub.core.security import utc_now
from vision_hub.domain.auth import Principal


class InMemoryTokenRevocationStore:
    def __init__(self, clock: Callable[[], datetime] = utc_now) -> None:
        self._revoked: dict[str, datetime] = {}
        self._clock = clock

    async def revoke(self, token_id: str, expires_at: datetime) -> bool:
        self._purge_expired()
        if token_id in self._revoked:
            return False
        self._revoked[token_id] = expires_at
        return True

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
