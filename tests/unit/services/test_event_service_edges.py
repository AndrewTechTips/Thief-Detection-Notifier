import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from vision_hub.core.errors import NotFoundError
from vision_hub.domain.motion import MotionEvent
from vision_hub.domain.storage import SnapshotKind
from vision_hub.infra.db.engine import Sessions
from vision_hub.infra.db.repositories.events import SqlEventRepository
from vision_hub.infra.storage.local import LocalSnapshotStore
from vision_hub.services.events import EventRecorder, EventService, RetentionService

type LogRecords = Callable[[], list[dict[str, Any]]]
T0 = datetime(2026, 10, 1, tzinfo=UTC)
EVENT = MotionEvent(id="00000000-0000-7000-8000-000000000001", device_id="porch", started_at=T0)


async def test_recorder_stop_without_start_is_harmless(tmp_path: Path) -> None:
    recorder = EventRecorder(
        SqlEventRepository.__new__(SqlEventRepository), LocalSnapshotStore(tmp_path), print
    )

    await recorder.stop()


async def test_missing_snapshot_file_is_not_found(sessions: Sessions, tmp_path: Path) -> None:
    repository, store = SqlEventRepository(sessions), LocalSnapshotStore(tmp_path / "snapshots")
    stored = await store.save(EVENT.id, SnapshotKind.CLEAN, b"x", T0)
    await repository.complete(EVENT, [], [stored])
    await store.delete(stored.path)  # e.g. removed by hand
    service = EventService(repository, store)

    with pytest.raises(NotFoundError, match="no clean snapshot"):
        await service.snapshot_file(EVENT.id, SnapshotKind.CLEAN)
    with pytest.raises(NotFoundError, match="no thumbnail snapshot"):
        await service.snapshot_file(EVENT.id, SnapshotKind.THUMBNAIL)
    assert [
        r.event.id for r in await service.after("00000000-0000-7000-8000-000000000000", limit=5)
    ] == [EVENT.id]


async def test_deleting_nothing(sessions: Sessions) -> None:
    assert await SqlEventRepository(sessions).delete([]) == 0


class TestRetentionLoop:
    async def test_keeps_running_after_a_failed_pass(
        self, sessions: Sessions, tmp_path: Path, log_records: LogRecords
    ) -> None:
        calls = 0

        async def flaky_overrides() -> dict[str, int]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ConnectionError("database down")
            return {}

        retention = RetentionService(
            SqlEventRepository(sessions),
            LocalSnapshotStore(tmp_path),
            default_days=30,
            overrides=flaky_overrides,
            interval_seconds=0.01,
        )
        retention.start()
        await asyncio.sleep(0.1)
        await retention.stop()

        assert calls >= 3
        assert any(r["event"] == "retention_failed" for r in log_records())

    async def test_stop_without_start(self, sessions: Sessions, tmp_path: Path) -> None:
        retention = RetentionService(
            SqlEventRepository(sessions),
            LocalSnapshotStore(tmp_path),
            default_days=30,
            overrides=lambda: asyncio.sleep(0, {}),
            interval_seconds=60,
        )

        await retention.stop()

    async def test_undeletable_files_are_logged_not_fatal(
        self, sessions: Sessions, tmp_path: Path, log_records: LogRecords
    ) -> None:
        class StubbornStore(LocalSnapshotStore):
            async def delete(self, path: str) -> None:
                raise PermissionError(path)

        repository, store = SqlEventRepository(sessions), StubbornStore(tmp_path)
        stored = await store.save(EVENT.id, SnapshotKind.CLEAN, b"x", T0)
        await repository.complete(EVENT, [], [stored])
        retention = RetentionService(
            repository,
            store,
            default_days=1,
            overrides=lambda: asyncio.sleep(0, {}),
            interval_seconds=60,
            clock=lambda: datetime(2027, 1, 1, tzinfo=UTC),
        )

        assert await retention.run_once() == 1  # the row is still removed
        assert any(r["event"] == "snapshot_delete_failed" for r in log_records())


async def test_failed_write_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_replace(self: Path, target: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", broken_replace)
    store = LocalSnapshotStore(tmp_path / "snapshots")

    with pytest.raises(OSError, match="disk full"):
        await store.save(EVENT.id, SnapshotKind.CLEAN, b"x", T0)

    assert [p for p in (tmp_path / "snapshots").rglob("*") if p.is_file()] == []
