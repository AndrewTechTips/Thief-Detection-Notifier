"""Audit trail: who changed which device or user, and when."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class AuditTarget(StrEnum):
    DEVICE = "device"
    USER = "user"


class AuditAction(StrEnum):
    DEVICE_CREATED = "device.created"
    DEVICE_UPDATED = "device.updated"
    DEVICE_DELETED = "device.deleted"
    DEVICE_STARTED = "device.started"
    DEVICE_STOPPED = "device.stopped"
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"  # password and/or role reset


@dataclass(frozen=True, slots=True)
class AuditEntry:
    id: str
    at: datetime
    actor: str
    action: str
    target_type: AuditTarget
    target_id: str
    details: Mapping[str, Any] = field(default_factory=dict)
    request_id: str | None = None


class AuditLog(Protocol):
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
    ) -> None: ...

    async def list(
        self,
        *,
        limit: int,
        target_type: AuditTarget | None = None,
        target_id: str | None = None,
        actor: str | None = None,
        before: tuple[datetime, str] | None = None,
    ) -> Sequence[AuditEntry]:
        """Newest first. ``before`` is the (at, id) keyset cursor."""
        ...
