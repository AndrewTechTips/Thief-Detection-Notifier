from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from vision_hub.domain.audit import AuditAction, AuditTarget
from vision_hub.domain.motion import MotionEvent
from vision_hub.domain.notifications import DeliveryStatus
from vision_hub.infra.db.engine import Sessions
from vision_hub.infra.db.models import NotificationRow
from vision_hub.infra.db.repositories.audit import SqlAuditLog
from vision_hub.infra.db.repositories.events import SqlEventRepository
from vision_hub.infra.db.repositories.notifications import SqlNotificationOutbox

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def event_id(n: int) -> str:
    return f"00000000-0000-7000-8000-{n:012d}"


async def record_event(sessions: Sessions, n: int, device_id: str = "porch") -> str:
    event = MotionEvent(id=event_id(n), device_id=device_id, started_at=T0, ended_at=T0)
    await SqlEventRepository(sessions).complete(event, [], [])
    return event.id


async def status_of(sessions: Sessions, delivery_id: str) -> NotificationRow:
    async with sessions() as session:
        row = await session.get(NotificationRow, delivery_id)
    assert row is not None
    return row


class TestOutbox:
    async def test_enqueue_is_idempotent_per_channel(self, sessions: Sessions) -> None:
        outbox = SqlNotificationOutbox(sessions)
        event = await record_event(sessions, 1)

        await outbox.enqueue(event, "porch", ["email", "webhook"], at=T0)
        await outbox.enqueue(event, "porch", ["email"], at=T0)  # e.g. replayed after a crash

        due = await outbox.due(T0, channels=["email", "webhook"], limit=10)
        assert sorted(d.channel for d in due) == ["email", "webhook"]
        assert await outbox.pending_count() == 2

    async def test_alerts_for_unrecorded_events_are_refused(self, sessions: Sessions) -> None:
        with pytest.raises(IntegrityError):
            await SqlNotificationOutbox(sessions).enqueue(event_id(9), "porch", ["email"], at=T0)

    async def test_due_respects_schedule_channels_and_limit(self, sessions: Sessions) -> None:
        outbox = SqlNotificationOutbox(sessions)
        for n in (1, 2, 3):
            await outbox.enqueue(await record_event(sessions, n), "porch", ["email"], at=T0)
        [first, *_] = await outbox.due(T0, channels=["email"], limit=10)
        await outbox.retry_later(
            first.id, attempts=1, next_attempt_at=T0 + timedelta(minutes=1), error="busy"
        )

        now = await outbox.due(T0, channels=["email"], limit=10)
        later = await outbox.due(T0 + timedelta(minutes=1), channels=["email"], limit=10)
        limited = await outbox.due(T0 + timedelta(minutes=1), channels=["email"], limit=1)
        other_channel = await outbox.due(T0, channels=["telegram"], limit=10)

        assert len(now) == 2
        assert len(later) == 3
        assert later[-1].id == first.id  # oldest schedule first
        assert later[-1].attempts == 1
        assert len(limited) == 1
        assert other_channel == []

    async def test_finished_deliveries_are_no_longer_due(self, sessions: Sessions) -> None:
        outbox = SqlNotificationOutbox(sessions)
        for n in (1, 2):
            await outbox.enqueue(await record_event(sessions, n), "porch", ["email"], at=T0)
        sent, failed = await outbox.due(T0, channels=["email"], limit=10)

        await outbox.mark_sent(sent.id, attempts=1, at=T0)
        await outbox.mark_failed(failed.id, attempts=3, at=T0, error="x" * 600)

        assert await outbox.due(T0, channels=["email"], limit=10) == []
        assert await outbox.pending_count() == 0
        sent_row, failed_row = (
            await status_of(sessions, sent.id),
            await status_of(sessions, failed.id),
        )
        assert (sent_row.status, sent_row.attempts, sent_row.finished_at) == ("sent", 1, T0)
        assert failed_row.status == DeliveryStatus.FAILED
        assert failed_row.last_error == "x" * 500  # truncated to the column size

    async def test_last_alert_per_device(self, sessions: Sessions) -> None:
        outbox = SqlNotificationOutbox(sessions)
        await outbox.enqueue(await record_event(sessions, 1), "porch", ["email"], at=T0)
        await outbox.enqueue(
            await record_event(sessions, 2), "porch", ["email"], at=T0 + timedelta(seconds=30)
        )
        await outbox.enqueue(await record_event(sessions, 3, "gate"), "gate", ["email"], at=T0)

        recent = await outbox.last_alerts(since=T0 + timedelta(seconds=10))
        everything = await outbox.last_alerts(since=T0)

        assert recent == {"porch": T0 + timedelta(seconds=30)}
        assert everything == {"porch": T0 + timedelta(seconds=30), "gate": T0}

    async def test_deliveries_go_with_their_event(self, sessions: Sessions) -> None:
        outbox = SqlNotificationOutbox(sessions)
        event = await record_event(sessions, 1)
        await outbox.enqueue(event, "porch", ["email"], at=T0)

        await SqlEventRepository(sessions).delete([event])  # retention

        assert await outbox.pending_count() == 0


class TestAuditLog:
    async def add(self, log: SqlAuditLog, n: int, **overrides: object) -> None:
        values: dict[str, object] = {
            "at": T0 + timedelta(minutes=n),
            "actor": "alice",
            "action": AuditAction.DEVICE_UPDATED,
            "target_type": AuditTarget.DEVICE,
            "target_id": "porch",
            "details": {"fields": ["name"]},
            "request_id": f"req-{n}",
        }
        await log.add(**(values | overrides))  # type: ignore[arg-type]

    async def test_newest_first_with_details(self, sessions: Sessions) -> None:
        log = SqlAuditLog(sessions)
        await self.add(log, 1)
        await self.add(log, 2, action=AuditAction.DEVICE_DELETED, details={})

        newest, oldest = await log.list(limit=10)

        assert (newest.action, newest.at) == ("device.deleted", T0 + timedelta(minutes=2))
        assert oldest.details == {"fields": ["name"]}
        assert (oldest.actor, oldest.target_type, oldest.request_id) == (
            "alice",
            AuditTarget.DEVICE,
            "req-1",
        )

    async def test_filters_and_keyset_pagination(self, sessions: Sessions) -> None:
        log = SqlAuditLog(sessions)
        await self.add(log, 1)
        await self.add(log, 2, actor="bob")
        await self.add(log, 3, target_type=AuditTarget.USER, target_id="carol")
        await self.add(log, 4)

        by_bob = await log.list(limit=10, actor="bob")
        users = await log.list(limit=10, target_type=AuditTarget.USER)
        porch = await log.list(limit=10, target_id="porch")
        first = await log.list(limit=2)
        second = await log.list(limit=2, before=(first[-1].at, first[-1].id))

        assert [e.request_id for e in by_bob] == ["req-2"]
        assert [e.target_id for e in users] == ["carol"]
        assert [e.request_id for e in porch] == ["req-4", "req-2", "req-1"]
        assert [e.request_id for e in [*first, *second]] == ["req-4", "req-3", "req-2", "req-1"]


class TestInterruptedEvents:
    async def test_open_events_are_flagged_once(self, sessions: Sessions) -> None:
        repository = SqlEventRepository(sessions)
        await repository.add_started(MotionEvent(id=event_id(1), device_id="porch", started_at=T0))
        await record_event(sessions, 2)  # ended normally

        flagged = await repository.mark_interrupted()
        again = await repository.mark_interrupted()

        interrupted, ended = await repository.get(event_id(1)), await repository.get(event_id(2))
        assert (flagged, again) == (1, 0)
        assert interrupted is not None
        assert (interrupted.interrupted, interrupted.complete) == (True, False)
        assert ended is not None
        assert ended.interrupted is False
