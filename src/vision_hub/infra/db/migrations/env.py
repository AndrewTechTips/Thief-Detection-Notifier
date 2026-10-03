"""Alembic environment: programmatic runs pass a connection; the CLI builds one from settings."""

import asyncio

from alembic import context
from sqlalchemy import Connection

from vision_hub.infra.db import models  # noqa: F401 - registers the tables on the metadata
from vision_hub.infra.db.base import Base


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite cannot ALTER most things
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_from_settings() -> None:
    from vision_hub.core.config import get_settings
    from vision_hub.infra.db.engine import create_engine

    engine = create_engine(get_settings().db)
    async with engine.begin() as connection:
        await connection.run_sync(run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) migrations are not supported; run against a database.")
if (connection := context.config.attributes.get("connection")) is not None:
    run_migrations(connection)
else:
    asyncio.run(run_from_settings())
