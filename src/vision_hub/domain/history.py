"""Recorded motion events, as stored in the event history."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from vision_hub.domain.motion import BoundingBox, MotionEvent
from vision_hub.domain.storage import SnapshotKind, StoredSnapshot


@dataclass(frozen=True, slots=True)
class EventRecord:
    event: MotionEvent
    boxes: tuple[BoundingBox, ...] = ()
    snapshots: dict[SnapshotKind, StoredSnapshot] = field(default_factory=dict)
    interrupted: bool = False  # the hub stopped abruptly before the event ended

    @property
    def complete(self) -> bool:
        """False while the event is in progress, and for interrupted events."""
        return self.event.ended_at is not None


class EventRepository(Protocol):
    async def add_started(self, event: MotionEvent) -> None: ...

    async def complete(
        self, event: MotionEvent, boxes: Sequence[BoundingBox], snapshots: Sequence[StoredSnapshot]
    ) -> None:
        """Store the finished event (inserting it if the start was never recorded)."""
        ...

    async def get(self, event_id: str) -> EventRecord | None: ...

    async def list(
        self,
        *,
        limit: int,
        device_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        before: tuple[datetime, str] | None = None,
        person: bool | None = None,
    ) -> Sequence[EventRecord]:
        """Newest first. ``before`` is the (started_at, id) keyset cursor; ``person`` keeps only
        events with (True) or without (False) a person seen; unchecked events match neither."""
        ...

    async def after(self, event_id: str, *, limit: int) -> Sequence[EventRecord]:
        """Events newer than ``event_id`` (UUIDv7 ids sort by time), oldest first."""
        ...

    async def expired(
        self,
        cutoff: datetime,
        *,
        limit: int,
        only_device: str | None = None,
        exclude_devices: Collection[str] = (),
    ) -> Sequence[EventRecord]: ...

    async def delete(self, event_ids: Collection[str]) -> int: ...

    async def mark_interrupted(self) -> int:
        """Flag events that never ended (the hub stopped abruptly); run before cameras start.
        Returns how many were flagged."""
        ...
