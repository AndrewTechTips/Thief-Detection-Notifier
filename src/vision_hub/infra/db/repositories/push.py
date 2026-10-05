from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, select, update

from vision_hub.domain.push import PushSubscription
from vision_hub.infra.db.engine import Sessions
from vision_hub.infra.db.models import PushSubscriptionRow


class SqlPushSubscriptionRepository:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def save(self, subscription: PushSubscription) -> PushSubscription:
        async with self._sessions.begin() as session:
            row = await session.get(PushSubscriptionRow, subscription.id)
            if row is None:
                row = PushSubscriptionRow(id=subscription.id, created_at=subscription.created_at)
                session.add(row)
            # Same browser, maybe another account or new keys: the latest subscription wins.
            row.username = subscription.username
            row.endpoint = subscription.endpoint
            row.p256dh = subscription.p256dh
            row.auth = subscription.auth
            await session.flush()
            return _to_domain(row)

    async def list(self, *, username: str | None = None) -> Sequence[PushSubscription]:
        query = select(PushSubscriptionRow).order_by(PushSubscriptionRow.created_at)
        if username is not None:
            query = query.where(PushSubscriptionRow.username == username)
        async with self._sessions() as session:
            return [_to_domain(row) for row in await session.scalars(query)]

    async def delete(self, subscription_id: str, *, username: str | None = None) -> bool:
        query = delete(PushSubscriptionRow).where(PushSubscriptionRow.id == subscription_id)
        if username is not None:
            query = query.where(PushSubscriptionRow.username == username)
        async with self._sessions.begin() as session:
            result = await session.execute(query)
        return bool(getattr(result, "rowcount", 0))

    async def mark_used(self, subscription_id: str, *, at: datetime) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(PushSubscriptionRow)
                .where(PushSubscriptionRow.id == subscription_id)
                .values(last_success_at=at)
            )


def _to_domain(row: PushSubscriptionRow) -> PushSubscription:
    return PushSubscription(
        id=row.id,
        username=row.username,
        endpoint=row.endpoint,
        p256dh=row.p256dh,
        auth=row.auth,
        created_at=row.created_at,
        last_success_at=row.last_success_at,
    )
