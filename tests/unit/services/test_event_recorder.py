from collections.abc import Collection, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vision_hub.domain.devices import DeviceStatus
from vision_hub.domain.events import (
    CameraEvent,
    DeviceStatusChanged,
    MotionEndedEvent,
    MotionStartedEvent,
)
from vision_hub.domain.history import EventRecord
from vision_hub.domain.motion import BoundingBox, MotionEvent
from vision_hub.domain.storage import SnapshotKind, StoredSnapshot
from vision_hub.infra.storage.local import LocalSnapshotStore
from vision_hub.services.events import EventRecorder

T0 = datetime(2026, 10, 3, tzinfo=UTC)
MOTION = MotionEvent(id="evt-1", device_id="porch", started_at=T0, ended_at=T0)


class FakeRepository:
    def __init__(self, log: list[str], *, broken: bool = False) -> None:
        self.log = log
        self.broken = broken
        self.completed: list[tuple[MotionEvent, Sequence[StoredSnapshot]]] = []

    async def add_started(self, event: MotionEvent) -> None:
        self._maybe_fail()
        self.log.append(f"stored start {event.id}")

    async def complete(
        self, event: MotionEvent, boxes: Sequence[BoundingBox], snapshots: Sequence[StoredSnapshot]
    ) -> None:
        self._maybe_fail()
        self.log.append(f"stored end {event.id}")
        self.completed.append((event, snapshots))

    def _maybe_fail(self) -> None:
        if self.broken:
            raise ConnectionError("database down")

    async def get(self, event_id: str) -> EventRecord | None:  # pragma: no cover - unused
        return None

    async def list(self, **_: object) -> Sequence[EventRecord]:  # pragma: no cover - unused
        return []

    async def after(
        self, event_id: str, *, limit: int
    ) -> Sequence[EventRecord]:  # pragma: no cover
        return []

    async def expired(self, *_: object, **__: object) -> Sequence[EventRecord]:  # pragma: no cover
        return []

    async def delete(self, event_ids: Collection[str]) -> int:  # pragma: no cover - unused
        return 0

    async def mark_interrupted(self) -> int:  # pragma: no cover - unused
        return 0


def ended() -> MotionEndedEvent:
    return MotionEndedEvent(
        event=MOTION, snapshot_jpeg=b"clean", annotated_jpeg=b"boxes", thumbnail_jpeg=b"small"
    )


@pytest.fixture
def log() -> list[str]:
    return []


def recorder(repository: FakeRepository, tmp_path: Path, log: list[str]) -> EventRecorder:
    def publish(event: CameraEvent) -> None:
        log.append(f"published {type(event).__name__}")

    return EventRecorder(repository, LocalSnapshotStore(tmp_path), publish)


async def test_persists_before_publishing(tmp_path: Path, log: list[str]) -> None:
    repository = FakeRepository(log)
    events = recorder(repository, tmp_path, log)
    events.start()

    events.submit(MotionStartedEvent(event=MOTION))
    events.submit(ended())
    await events.stop()

    assert log == [
        "stored start evt-1",
        "published MotionStartedEvent",
        "stored end evt-1",
        "published MotionEndedEvent",
    ]
    [(_, snapshots)] = repository.completed
    assert {s.kind for s in snapshots} == set(SnapshotKind)
    assert (tmp_path / snapshots[0].path).is_file()


async def test_still_publishes_when_recording_fails(
    tmp_path: Path, log: list[str], log_records: object
) -> None:
    events = recorder(FakeRepository(log, broken=True), tmp_path, log)
    events.start()

    events.submit(ended())
    await events.stop()

    assert log == ["published MotionEndedEvent"]  # the alert is not lost


async def test_status_changes_are_published_without_storage(tmp_path: Path, log: list[str]) -> None:
    events = recorder(FakeRepository(log), tmp_path, log)
    events.start()

    events.submit(DeviceStatusChanged(device_id="porch", status=DeviceStatus.ONLINE, at=T0))
    await events.stop()

    assert log == ["published DeviceStatusChanged"]


async def test_missing_thumbnail_is_skipped(tmp_path: Path, log: list[str]) -> None:
    repository = FakeRepository(log)
    events = recorder(repository, tmp_path, log)
    events.start()

    events.submit(MotionEndedEvent(event=MOTION, snapshot_jpeg=b"c", annotated_jpeg=b"a"))
    await events.stop()

    [(_, snapshots)] = repository.completed
    assert {s.kind for s in snapshots} == {SnapshotKind.CLEAN, SnapshotKind.ANNOTATED}
