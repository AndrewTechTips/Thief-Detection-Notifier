"""Event history: recording (persist, then publish), queries, and retention."""

import asyncio
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path

from vision_hub.core.errors import NotFoundError
from vision_hub.core.logging import get_logger
from vision_hub.core.metrics import Metrics
from vision_hub.core.security import utc_now
from vision_hub.core.tasks import TaskSupervisor
from vision_hub.domain.events import CameraEvent, MotionEndedEvent, MotionStartedEvent
from vision_hub.domain.history import EventRecord, EventRepository
from vision_hub.domain.motion import MotionEvent
from vision_hub.domain.storage import SnapshotKind, SnapshotStore, StoredSnapshot

logger = get_logger(__name__)


class EventRecorder:
    """Sits between the cameras and the event bus. Events are handled one at a time, in order:
    motion is written to the database (and snapshots to disk) *before* it is published, so
    anything a client sees can be fetched again later.

    If recording fails, the event is still published: losing an intrusion alert would be worse
    than a gap in the history.
    """

    def __init__(
        self,
        repository: EventRepository,
        store: SnapshotStore,
        publish: Callable[[CameraEvent], None],
        *,
        tasks: TaskSupervisor | None = None,
        metrics: Metrics | None = None,
    ) -> None:
        self._repository = repository
        self._store = store
        self._publish = publish
        self._tasks = tasks or TaskSupervisor()
        self._metrics = metrics or Metrics(process_metrics=False)
        self._queue: asyncio.Queue[CameraEvent] = asyncio.Queue()  # unbounded: events are rare
        self._task: asyncio.Task[None] | None = None

    def submit(self, event: CameraEvent) -> None:
        """Called on the loop thread (the camera manager's event callback)."""
        self._queue.put_nowait(event)

    def start(self) -> None:
        self._task = self._tasks.spawn("event-recorder", self._run)

    async def stop(self) -> None:
        """Record and publish everything already submitted, then stop."""
        self._queue.shutdown()
        if self._task is not None:
            await self._task

    async def _run(self) -> None:
        while True:
            try:
                event = await self._queue.get()
            except asyncio.QueueShutDown:
                return
            try:
                await self._record(event)
            except Exception:
                self._metrics.event_record_failures.inc()
                logger.exception("event_record_failed", event_type=type(event).__name__)
            finally:
                self._publish(event)

    async def _record(self, event: CameraEvent) -> None:
        match event:
            case MotionStartedEvent(event=motion):
                await self._repository.add_started(motion)
            case MotionEndedEvent(event=motion):
                files = {
                    SnapshotKind.CLEAN: event.snapshot_jpeg,
                    SnapshotKind.ANNOTATED: event.annotated_jpeg,
                    SnapshotKind.THUMBNAIL: event.thumbnail_jpeg,
                    SnapshotKind.CLIP: event.clip.data if event.clip else b"",
                }
                stored = [
                    await self._store.save(motion.id, kind, data, motion.started_at)
                    for kind, data in files.items()
                    if data
                ]
                await self._repository.complete(motion, event.boxes, stored)
            case _:
                pass  # device status changes are live-only


class EventService:
    def __init__(self, repository: EventRepository, store: SnapshotStore) -> None:
        self._repository = repository
        self._store = store

    async def list(
        self,
        *,
        limit: int,
        device_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        before: tuple[datetime, str] | None = None,
    ) -> Sequence[EventRecord]:
        return await self._repository.list(
            limit=limit, device_id=device_id, since=since, until=until, before=before
        )

    async def get(self, event_id: str) -> EventRecord:
        record = await self._repository.get(event_id)
        if record is None:
            raise NotFoundError(f"Event '{event_id}' not found.", event_id=event_id)
        return record

    async def snapshot_file(self, event_id: str, kind: SnapshotKind) -> Path:
        snapshot = (await self.get(event_id)).snapshots.get(kind)
        path = self._store.resolve(snapshot.path) if snapshot else None
        if path is None or not path.is_file():
            raise NotFoundError(f"Event '{event_id}' has no {kind} snapshot.", event_id=event_id)
        return path

    async def clip_file(self, event_id: str) -> tuple[Path, EventRecord]:
        """The event's clip, and the event (for naming the download)."""
        record = await self.get(event_id)
        clip = record.snapshots.get(SnapshotKind.CLIP)
        path = self._store.resolve(clip.path) if clip else None
        if path is None or not path.is_file():
            raise NotFoundError(f"Event '{event_id}' has no clip.", event_id=event_id)
        return path, record

    async def after(self, event_id: str, *, limit: int) -> Sequence[EventRecord]:
        return await self._repository.after(event_id, limit=limit)

    async def evidence(self, event_id: str) -> tuple[MotionEvent, bytes] | None:
        """The event and its annotated image, for alerts; the image is empty if it is gone."""
        record = await self._repository.get(event_id)
        if record is None:
            return None
        snapshot = record.snapshots.get(SnapshotKind.ANNOTATED)
        image = b""
        if snapshot is not None:
            try:
                image = await self._store.read(snapshot.path)
            except OSError, ValueError:
                logger.warning("evidence_image_unavailable", event_id=event_id)
        return record.event, image


class RetentionService:
    """Deletes events (rows and snapshot files) older than their retention period: the
    device's ``retention_days`` if set, the hub-wide default otherwise (also for the history of
    deleted devices)."""

    BATCH = 500

    def __init__(
        self,
        repository: EventRepository,
        store: SnapshotStore,
        *,
        default_days: int,
        overrides: Callable[[], Awaitable[Mapping[str, int]]],
        interval_seconds: float,
        tasks: TaskSupervisor | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._repository = repository
        self._store = store
        self._default = timedelta(days=default_days)
        self._overrides = overrides
        self._interval = interval_seconds
        self._tasks = tasks or TaskSupervisor()
        self._clock = clock
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = self._tasks.spawn("retention", self._loop)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def run_once(self) -> int:
        overrides = await self._overrides()
        now = self._clock()
        deleted = await self._purge(now - self._default, exclude_devices=overrides.keys())
        for device_id, days in overrides.items():
            deleted += await self._purge(now - timedelta(days=days), only_device=device_id)
        if deleted:
            logger.info("events_expired", deleted=deleted)
        return deleted

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                logger.exception("retention_failed")
            await asyncio.sleep(self._interval)

    async def _purge(
        self,
        cutoff: datetime,
        *,
        only_device: str | None = None,
        exclude_devices: Collection[str] = (),
    ) -> int:
        deleted = 0
        while records := await self._repository.expired(
            cutoff, limit=self.BATCH, only_device=only_device, exclude_devices=exclude_devices
        ):
            for record in records:
                for snapshot in record.snapshots.values():
                    await self._delete_file(snapshot)
            deleted += await self._repository.delete([r.event.id for r in records])
        return deleted

    async def _delete_file(self, snapshot: StoredSnapshot) -> None:
        try:
            await self._store.delete(snapshot.path)
        except OSError, ValueError:
            logger.warning("snapshot_delete_failed", path=snapshot.path)
