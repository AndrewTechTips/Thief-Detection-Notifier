"""Async engine and session factory."""

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from vision_hub.core.config import DatabaseConfig

type Sessions = async_sessionmaker[AsyncSession]


def create_engine(config: DatabaseConfig) -> AsyncEngine:
    url = config.sqlalchemy_url()
    options: dict[str, Any] = {"echo": config.echo}
    if url.get_backend_name() == "postgresql":
        options |= {"pool_size": config.pool_size, "pool_pre_ping": True}
    engine = create_async_engine(url, **options)
    if url.get_backend_name() == "sqlite":
        event.listen(engine.sync_engine, "connect", _sqlite_pragmas)
    return engine


def create_sessions(engine: AsyncEngine) -> Sessions:
    # expire_on_commit=False: rows stay readable after commit (we convert them right away).
    return async_sessionmaker(engine, expire_on_commit=False)


def _sqlite_pragmas(connection: Any, _record: Any) -> None:
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
