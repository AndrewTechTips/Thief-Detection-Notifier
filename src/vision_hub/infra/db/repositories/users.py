from sqlalchemy import func, select

from vision_hub.domain.auth import Role, User
from vision_hub.infra.db.engine import Sessions
from vision_hub.infra.db.models import UserRow


class SqlUserRepository:
    def __init__(self, sessions: Sessions) -> None:
        self._sessions = sessions

    async def get_by_username(self, username: str) -> User | None:
        async with self._sessions() as session:
            row = await session.scalar(select(UserRow).where(UserRow.username == username))
        return _to_user(row) if row else None

    async def count(self) -> int:
        async with self._sessions() as session:
            return await session.scalar(select(func.count()).select_from(UserRow)) or 0

    async def save(self, username: str, password_hash: str, role: Role) -> bool:
        """Create the user, or update password and role; returns True if it was created."""
        async with self._sessions.begin() as session:
            row = await session.scalar(select(UserRow).where(UserRow.username == username))
            if row is None:
                session.add(UserRow(username=username, password_hash=password_hash, role=role))
                return True
            row.password_hash = password_hash
            row.role = role.value
            return False

    async def create_if_missing(self, username: str, password_hash: str, role: Role) -> bool:
        if await self.get_by_username(username) is not None:
            return False
        return await self.save(username, password_hash, role)


def _to_user(row: UserRow) -> User:
    return User(username=row.username, role=Role(row.role), password_hash=row.password_hash)
