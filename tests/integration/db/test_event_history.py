from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from vision_hub.domain.motion import BoundingBox, MotionEvent
from vision_hub.domain.storage import SnapshotKind, StoredSnapshot
from vision_hub.infra.db.engine import Sessions
from vision_hub.infra.db.models import SnapshotRow
from vision_hub.infra.db.repositories.events import SqlEventRepository
from vision_hub.infra.storage.local import LocalSnapshotStore
from vision_hub.services.events import RetentionService

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def event(n: int, device_id: str = "porch", *, started: datetime | None = None) -> MotionEvent:
    """Ids are zero-padded so they sort like UUIDv7 (time-ordered)."""
    start = started or T0 + timedelta(minutes=n)
    return MotionEvent(
        id=f"00000000-0000-7000-8000-{n:012d}",
        device_id=device_id,
        started_at=start,
        ended_at=start + timedelta(seconds=5),
        peak_area_ratio=0.1,
        motion_frames=50,
    )


def snapshot(kind: SnapshotKind, n: int) -> StoredSnapshot:
    return StoredSnapshot(kind=kind, path=f"2026/10/01/{n}-{kind}.jpg", size_bytes=100)


class TestRecording:
    async def test_start_then_complete(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        started = MotionEvent(id=event(1).id, device_id="porch", started_at=T0)

        await repository.add_started(started)
        interrupted = await repository.get(started.id)
        await repository.complete(
            event(1), [BoundingBox(1, 2, 3, 4)], [snapshot(SnapshotKind.CLEAN, 1)]
        )
        record = await repository.get(started.id)

        assert interrupted is not None
        assert interrupted.complete is False
        assert record is not None
        assert record.complete is True
        assert record.boxes == (BoundingBox(1, 2, 3, 4),)
        assert record.snapshots[SnapshotKind.CLEAN].path == "2026/10/01/1-clean.jpg"
        assert record.event.ended_at == event(1).ended_at

    async def test_complete_without_a_recorded_start(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)

        await repository.complete(event(2), [], [])

        assert await repository.get(event(2).id) is not None

    async def test_unknown_event(self, sessions: Sessions) -> None:
        assert await SqlEventRepository(sessions).get(event(99).id) is None


class TestQueries:
    async def seed(self, repository: SqlEventRepository) -> None:
        for n in range(1, 7):
            await repository.complete(event(n, "porch" if n % 2 else "gate"), [], [])

    async def test_newest_first_with_filters(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        await self.seed(repository)

        everything = await repository.list(limit=10)
        porch = await repository.list(limit=10, device_id="porch")
        window = await repository.list(
            limit=10, since=T0 + timedelta(minutes=2), until=T0 + timedelta(minutes=4)
        )

        assert [r.event.id[-1] for r in everything] == list("654321")
        assert [r.event.id[-1] for r in porch] == list("531")
        assert [r.event.id[-1] for r in window] == list("32")

    async def test_keyset_pagination(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        await self.seed(repository)

        first = await repository.list(limit=4)
        last = first[-1].event
        second = await repository.list(limit=4, before=(last.started_at, last.id))

        assert [r.event.id[-1] for r in first] == list("6543")
        assert [r.event.id[-1] for r in second] == list("21")

    async def test_events_after_an_id(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        await self.seed(repository)

        newer = await repository.after(event(3).id, limit=10)

        assert [r.event.id[-1] for r in newer] == list("456")  # oldest first


class TestRetention:
    async def test_deletes_rows_and_files_per_device_policy(
        self, sessions: Sessions, tmp_path: Path
    ) -> None:
        repository = SqlEventRepository(sessions)
        store = LocalSnapshotStore(tmp_path / "snapshots")
        now = T0 + timedelta(days=40)
        ages = {"porch": 35, "gate": 35, "deleted-camera": 35, "porch-new": 5}
        for n, (device, days) in enumerate(ages.items(), start=1):
            device_id = device.removesuffix("-new")
            started = now - timedelta(days=days)
            stored = await store.save(event(n).id, SnapshotKind.CLEAN, b"jpeg", started)
            await repository.complete(event(n, device_id, started=started), [], [stored])

        retention = RetentionService(
            repository,
            store,
            default_days=30,
            overrides=lambda: _async({"gate": 90}),  # gate keeps events for 90 days
            interval_seconds=3600,
            clock=lambda: now,
        )
        deleted = await retention.run_once()

        remaining = {r.event.device_id for r in await repository.list(limit=10)}
        assert deleted == 2  # porch (35 d > 30 d) and the deleted camera's history
        assert remaining == {"gate", "porch"}  # gate: 35 d < 90 d; porch: the 5-day-old event
        assert len(list((tmp_path / "snapshots").rglob("*.jpg"))) == 2
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(SnapshotRow)) == 2

    async def test_nothing_to_delete(self, sessions: Sessions, tmp_path: Path) -> None:
        retention = RetentionService(
            SqlEventRepository(sessions),
            LocalSnapshotStore(tmp_path),
            default_days=30,
            overrides=lambda: _async({}),
            interval_seconds=3600,
        )

        assert await retention.run_once() == 0


async def _async(value: dict[str, int]) -> dict[str, int]:
    return value
