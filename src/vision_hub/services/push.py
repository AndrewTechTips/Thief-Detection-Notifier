"""Web push subscriptions: which browsers get alerts, and a test notification to check one."""

from collections.abc import Callable, Sequence
from datetime import datetime

from vision_hub.core.errors import NotFoundError, PushServiceNotAllowedError
from vision_hub.core.security import utc_now
from vision_hub.domain.auth import Principal
from vision_hub.domain.push import (
    PushSender,
    PushSubscription,
    PushSubscriptionRepository,
    PushTestResult,
    endpoint_allowed,
    subscription_id,
)


class PushService:
    def __init__(
        self,
        subscriptions: PushSubscriptionRepository,
        sender: PushSender,
        *,
        allowed_hosts: Sequence[str],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._subscriptions = subscriptions
        self._sender = sender
        self._allowed_hosts = list(allowed_hosts)
        self._clock = clock

    @property
    def public_key(self) -> str:
        return self._sender.public_key

    async def subscribe(
        self, principal: Principal, *, endpoint: str, p256dh: str, auth: str
    ) -> PushSubscription:
        """Saves the browser's subscription for this user; subscribing again replaces it."""
        if not endpoint_allowed(endpoint, self._allowed_hosts):
            msg = "The endpoint is not on a push service this hub sends to."
            raise PushServiceNotAllowedError(msg)
        return await self._subscriptions.save(
            PushSubscription(
                id=subscription_id(endpoint),
                username=principal.username,
                endpoint=endpoint,
                p256dh=p256dh,
                auth=auth,
                created_at=self._clock(),
            )
        )

    async def unsubscribe(self, principal: Principal, subscription_id: str) -> None:
        """Only your own browsers: another user's subscription is reported as not found."""
        if not await self._subscriptions.delete(subscription_id, username=principal.username):
            msg = "No such subscription."
            raise NotFoundError(msg)

    async def send_test(self, principal: Principal) -> PushTestResult:
        return await self._sender.send_test(principal.username)
