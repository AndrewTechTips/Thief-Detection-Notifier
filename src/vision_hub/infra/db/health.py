from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class DatabaseHealthCheck:
    """Readiness: the database answers a trivial query."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @property
    def name(self) -> str:
        return "database"

    async def check(self) -> None:
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
