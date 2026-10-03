"""Records administrative changes in the audit trail."""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

import structlog

from vision_hub.core.logging import get_logger
from vision_hub.core.security import utc_now
from vision_hub.domain.audit import AuditAction, AuditEntry, AuditLog, AuditTarget

logger = get_logger(__name__)

SYSTEM_ACTOR = "system"  # changes made by the hub itself (e.g. the admin created on first start)


class Auditor:
    """The change being recorded has already happened, so a failed audit write never fails
    the request: it is logged with every detail instead, the logs being the fallback trail."""

    def __init__(self, log: AuditLog, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._log = log
        self._clock = clock

    async def record(
        self,
        actor: str,
        action: AuditAction,
        target_type: AuditTarget,
        target_id: str,
        **details: Any,
    ) -> None:
        # Correlates the entry with the request's log lines.
        request_id = structlog.contextvars.get_contextvars().get("request_id")
        try:
            await self._log.add(
                at=self._clock(),
                actor=actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                details=details,
                request_id=request_id,
            )
        except Exception:
            logger.exception(
                "audit_write_failed",
                actor=actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                details=details,
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
        return await self._log.list(
            limit=limit, target_type=target_type, target_id=target_id, actor=actor, before=before
        )
