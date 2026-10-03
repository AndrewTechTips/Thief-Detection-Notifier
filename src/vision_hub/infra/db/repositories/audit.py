from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import literal, select, tuple_

from vision_hub.domain.audit import AuditAction, AuditEntry, AuditTarget
from vision_hub.infra.db.engine import Sessions
from vision_hub.infra.db.models import AuditLogRow


class SqlAuditLog:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def add(
        self,
        *,
        at: datetime,
        actor: str,
        action: AuditAction,
        target_type: AuditTarget,
        target_id: str,
        details: Mapping[str, Any],
        request_id: str | None,
    ) -> None:
        async with self._sessions.begin() as session:
            session.add(
                AuditLogRow(
                    at=at,
                    actor=actor,
                    action=action.value,
                    target_type=target_type.value,
                    target_id=target_id,
                    details=dict(details),
                    request_id=request_id,
                )
            )

    async def list(
        self,
        *,
        limit: int,
        target_type: AuditTarget | None = None,
        target_id: str | None = None,
        actor: str | None = None,
        before: tuple[datetime, str] | None = None,
    ) -> Sequence[AuditEntry]:
        query = select(AuditLogRow)
        if target_type is not None:
            query = query.where(AuditLogRow.target_type == target_type.value)
        if target_id is not None:
            query = query.where(AuditLogRow.target_id == target_id)
        if actor is not None:
            query = query.where(AuditLogRow.actor == actor)
        if before is not None:
            at, entry_id = before
            query = query.where(
                tuple_(AuditLogRow.at, AuditLogRow.id)
                < tuple_(literal(at, AuditLogRow.at.type), literal(entry_id, AuditLogRow.id.type))
            )
        query = query.order_by(AuditLogRow.at.desc(), AuditLogRow.id.desc()).limit(limit)
        async with self._sessions() as session:
            rows = (await session.scalars(query)).all()
        return [
            AuditEntry(
                id=row.id,
                at=row.at,
                actor=row.actor,
                action=row.action,
                target_type=AuditTarget(row.target_type),
                target_id=row.target_id,
                details=row.details,
                request_id=row.request_id,
            )
            for row in rows
        ]
