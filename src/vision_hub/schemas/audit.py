from datetime import datetime
from typing import Any

from vision_hub.domain.audit import AuditEntry, AuditTarget
from vision_hub.schemas.base import ApiSchema


class AuditEntryOut(ApiSchema):
    id: str
    at: datetime
    actor: str
    action: str
    target_type: AuditTarget
    target_id: str
    details: dict[str, Any]
    request_id: str | None

    @classmethod
    def from_entry(cls, entry: AuditEntry) -> AuditEntryOut:
        return cls(
            id=entry.id,
            at=entry.at,
            actor=entry.actor,
            action=entry.action,
            target_type=entry.target_type,
            target_id=entry.target_id,
            details=dict(entry.details),
            request_id=entry.request_id,
        )
