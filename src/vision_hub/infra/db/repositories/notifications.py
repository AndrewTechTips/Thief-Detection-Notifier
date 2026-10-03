from collections.abc import Collection, Mapping, Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from vision_hub.domain.notifications import Delivery, DeliveryStatus
from vision_hub.infra.db.engine import Sessions
from vision_hub.infra.db.models import NotificationRow

ERROR_LENGTH = 500


class SqlNotificationOutbox:
    """Single node: deliveries in flight are tracked by the service, so rows are not locked.
    Several dispatchers would claim rows with ``SELECT ... FOR UPDATE SKIP LOCKED``."""

    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def enqueue(
        self, event_id: str, device_id: str, channels: Sequence[str], *, at: datetime
    ) -> None:
        for channel in channels:
            try:
                async with self._sessions.begin() as session:
                    session.add(
                        NotificationRow(
                            event_id=event_id,
                            device_id=device_id,
                            channel=channel,
                            next_attempt_at=at,
                            created_at=at,
                        )
                    )
            except IntegrityError:
                if not await self._exists(event_id, channel):
                    raise  # not a duplicate: e.g. the event itself was never recorded

    async def due(
        self, now: datetime, *, channels: Collection[str], limit: int
    ) -> Sequence[Delivery]:
        query = (
            select(NotificationRow)
            .where(
                NotificationRow.status == DeliveryStatus.PENDING,
                NotificationRow.next_attempt_at <= now,
                NotificationRow.channel.in_(channels),
            )
            .order_by(NotificationRow.next_attempt_at, NotificationRow.id)
            .limit(limit)
        )
        async with self._sessions() as session:
            rows = (await session.scalars(query)).all()
        return [
            Delivery(
                id=row.id,
                event_id=row.event_id,
                device_id=row.device_id,
                channel=row.channel,
                attempts=row.attempts,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def mark_sent(self, delivery_id: str, *, attempts: int, at: datetime) -> None:
        await self._update(
            delivery_id, status=DeliveryStatus.SENT, attempts=attempts, finished_at=at
        )

    async def mark_failed(
        self, delivery_id: str, *, attempts: int, at: datetime, error: str
    ) -> None:
        await self._update(
            delivery_id,
            status=DeliveryStatus.FAILED,
            attempts=attempts,
            finished_at=at,
            last_error=error[:ERROR_LENGTH],
        )

    async def retry_later(
        self, delivery_id: str, *, attempts: int, next_attempt_at: datetime, error: str
    ) -> None:
        await self._update(
            delivery_id,
            attempts=attempts,
            next_attempt_at=next_attempt_at,
            last_error=error[:ERROR_LENGTH],
        )

    async def pending_count(self) -> int:
        query = select(func.count()).where(NotificationRow.status == DeliveryStatus.PENDING)
        async with self._sessions() as session:
            return await session.scalar(query) or 0

    async def last_alerts(self, *, since: datetime) -> Mapping[str, datetime]:
        query = (
            select(NotificationRow.device_id, func.max(NotificationRow.created_at))
            .where(NotificationRow.created_at >= since)
            .group_by(NotificationRow.device_id)
        )
        async with self._sessions() as session:
            rows = (await session.execute(query)).all()
        return {device_id: last for device_id, last in rows}  # noqa: C416 - typed unpacking

    async def _exists(self, event_id: str, channel: str) -> bool:
        query = select(NotificationRow.id).where(
            NotificationRow.event_id == event_id, NotificationRow.channel == channel
        )
        async with self._sessions() as session:
            return await session.scalar(query) is not None

    async def _update(self, delivery_id: str, **values: object) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(NotificationRow).where(NotificationRow.id == delivery_id).values(**values)
            )
