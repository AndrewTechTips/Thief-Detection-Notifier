from collections.abc import Collection, Sequence
from datetime import datetime

from sqlalchemy import Select, delete, literal, select, tuple_

from vision_hub.domain.history import EventRecord
from vision_hub.domain.motion import BoundingBox, MotionEvent
from vision_hub.domain.storage import SnapshotKind, StoredSnapshot
from vision_hub.infra.db.engine import Sessions
from vision_hub.infra.db.models import MotionEventRow, SnapshotRow


class SqlEventRepository:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def add_started(self, event: MotionEvent) -> None:
        async with self._sessions.begin() as session:
            session.add(_new_row(event))

    async def complete(
        self, event: MotionEvent, boxes: Sequence[BoundingBox], snapshots: Sequence[StoredSnapshot]
    ) -> None:
        async with self._sessions.begin() as session:
            row = await session.get(MotionEventRow, event.id)
            if row is None:  # the start was never recorded (e.g. the database was down)
                row = _new_row(event)
                session.add(row)
            row.ended_at = event.ended_at
            row.peak_area_ratio = event.peak_area_ratio
            row.motion_frames = event.motion_frames
            row.boxes = [{"x": b.x, "y": b.y, "width": b.width, "height": b.height} for b in boxes]
            row.snapshots = [
                SnapshotRow(kind=s.kind.value, path=s.path, size_bytes=s.size_bytes)
                for s in snapshots
            ]

    async def get(self, event_id: str) -> EventRecord | None:
        async with self._sessions() as session:
            row = await session.get(MotionEventRow, event_id)
        return _to_record(row) if row else None

    async def list(
        self,
        *,
        limit: int,
        device_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        before: tuple[datetime, str] | None = None,
    ) -> Sequence[EventRecord]:
        query = select(MotionEventRow)
        if device_id is not None:
            query = query.where(MotionEventRow.device_id == device_id)
        if since is not None:
            query = query.where(MotionEventRow.started_at >= since)
        if until is not None:
            query = query.where(MotionEventRow.started_at < until)
        if before is not None:
            # Typed literals: PostgreSQL will not compare a uuid column with a VARCHAR.
            started_at, event_id = before
            query = query.where(
                tuple_(MotionEventRow.started_at, MotionEventRow.id)
                < tuple_(
                    literal(started_at, MotionEventRow.started_at.type),
                    literal(event_id, MotionEventRow.id.type),
                )
            )
        query = query.order_by(MotionEventRow.started_at.desc(), MotionEventRow.id.desc())
        return await self._fetch(query.limit(limit))

    async def after(self, event_id: str, *, limit: int) -> Sequence[EventRecord]:
        query = (
            select(MotionEventRow)
            .where(MotionEventRow.id > event_id)
            .order_by(MotionEventRow.id)
            .limit(limit)
        )
        return await self._fetch(query)

    async def expired(
        self,
        cutoff: datetime,
        *,
        limit: int,
        only_device: str | None = None,
        exclude_devices: Collection[str] = (),
    ) -> Sequence[EventRecord]:
        query = select(MotionEventRow).where(MotionEventRow.started_at < cutoff)
        if only_device is not None:
            query = query.where(MotionEventRow.device_id == only_device)
        if exclude_devices:
            query = query.where(MotionEventRow.device_id.not_in(exclude_devices))
        return await self._fetch(query.order_by(MotionEventRow.started_at).limit(limit))

    async def delete(self, event_ids: Collection[str]) -> int:
        if not event_ids:
            return 0
        async with self._sessions.begin() as session:
            # Snapshot rows go with them (ON DELETE CASCADE).
            result = await session.execute(
                delete(MotionEventRow).where(MotionEventRow.id.in_(event_ids))
            )
        return int(getattr(result, "rowcount", 0))

    async def _fetch(self, query: Select[MotionEventRow]) -> Sequence[EventRecord]:
        async with self._sessions() as session:
            rows = await session.scalars(query)
            return [_to_record(row) for row in rows]


def _new_row(event: MotionEvent) -> MotionEventRow:
    return MotionEventRow(
        id=event.id,
        device_id=event.device_id,
        started_at=event.started_at,
        ended_at=event.ended_at,
        peak_area_ratio=event.peak_area_ratio,
        motion_frames=event.motion_frames,
        boxes=[],
        snapshots=[],
    )


def _to_record(row: MotionEventRow) -> EventRecord:
    return EventRecord(
        event=MotionEvent(
            id=row.id,
            device_id=row.device_id,
            started_at=row.started_at,
            ended_at=row.ended_at,
            peak_area_ratio=row.peak_area_ratio,
            motion_frames=row.motion_frames,
        ),
        boxes=tuple(BoundingBox(**box) for box in row.boxes),
        snapshots={
            SnapshotKind(s.kind): StoredSnapshot(SnapshotKind(s.kind), s.path, s.size_bytes)
            for s in row.snapshots
        },
    )
