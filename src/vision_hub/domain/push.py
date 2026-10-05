"""Browsers subscribed to web push alerts."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class PushSubscription:
    """Where a browser receives alerts and the keys to encrypt them for it. ``id`` is derived
    from the endpoint, so subscribing the same browser again replaces the old entry."""

    id: str
    username: str
    endpoint: str
    p256dh: str  # the browser's public key, base64url
    auth: str  # shared authentication secret, base64url
    created_at: datetime
    last_success_at: datetime | None = None


class PushSubscriptionRepository(Protocol):
    async def save(self, subscription: PushSubscription) -> PushSubscription:
        """Insert, or replace the subscription with the same id (keeping ``created_at``)."""
        ...

    async def list(self, *, username: str | None = None) -> Sequence[PushSubscription]: ...

    async def delete(self, subscription_id: str, *, username: str | None = None) -> bool:
        """Delete it (only if it belongs to ``username``, when given); False if not found."""
        ...

    async def mark_used(self, subscription_id: str, *, at: datetime) -> None: ...


@dataclass(frozen=True, slots=True)
class PushTestResult:
    delivered: int  # accepted by the browser's push service
    failed: int  # push service unreachable or refused it


class PushSender(Protocol):
    @property
    def public_key(self) -> str:
        """The key browsers subscribe with (``applicationServerKey``), base64url."""
        ...

    async def send_test(self, username: str) -> PushTestResult:
        """A test notification to the user's browsers."""
        ...


def subscription_id(endpoint: str) -> str:
    """Stable id for a subscription: a browser that subscribes again replaces its old entry."""
    return hashlib.sha256(endpoint.encode()).hexdigest()


def endpoint_allowed(endpoint: str, allowed_hosts: Sequence[str]) -> bool:
    """HTTPS on a known push service (that host or a subdomain of it). The hub POSTs to
    subscription endpoints, so anything else would let a user point it at internal services."""
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts)
