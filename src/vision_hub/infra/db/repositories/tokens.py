from collections.abc import Callable
from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from vision_hub.core.security import utc_now
from vision_hub.infra.db.engine import Sessions
from vision_hub.infra.db.models import RevokedTokenRow


class SqlTokenRevocationStore:
    """Atomic via the primary key: inserting an already-revoked ID fails, so two concurrent
    refreshes with the same token cannot both succeed, even across processes."""

    def __init__(self, sessions: Sessions, clock: Callable[[], datetime] = utc_now) -> None:
        self._sessions = sessions
        self._clock = clock

    async def revoke(self, token_id: str, expires_at: datetime) -> bool:
        try:
            async with self._sessions.begin() as session:
                session.add(RevokedTokenRow(token_id=token_id, expires_at=expires_at))
        except IntegrityError:
            return False
        await self.purge_expired()
        return True

    async def purge_expired(self) -> None:
        """Expired tokens fail signature checks anyway; their entries are no longer needed."""
        async with self._sessions.begin() as session:
            await session.execute(
                delete(RevokedTokenRow).where(RevokedTokenRow.expires_at <= self._clock())
            )
