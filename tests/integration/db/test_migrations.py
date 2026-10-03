from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, inspect
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from vision_hub.infra.db.base import Base, UtcDateTime
from vision_hub.infra.db.migrate import downgrade_to_base, upgrade_to_head


async def test_models_match_the_migrations(engine: AsyncEngine) -> None:
    """Fails when a model changes without a migration (or vice versa)."""

    def differences(connection: Connection) -> list[object]:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return list(compare_metadata(context, Base.metadata))

    async with engine.connect() as connection:
        assert await connection.run_sync(differences) == []


async def test_upgrade_and_downgrade_on_an_empty_database(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")

    def tables(connection: Connection) -> set[str]:
        return set(inspect(connection).get_table_names()) - {"alembic_version"}

    try:
        await upgrade_to_head(engine)
        async with engine.connect() as connection:
            assert await connection.run_sync(tables) == set(Base.metadata.tables)

        await upgrade_to_head(engine)  # idempotent: already at head

        await downgrade_to_base(engine)
        async with engine.connect() as connection:
            assert await connection.run_sync(tables) == set()
    finally:
        await engine.dispose()


def test_utc_type_passes_nulls_through() -> None:
    column_type, dialect = UtcDateTime(), sqlite.dialect()

    assert column_type.process_bind_param(None, dialect) is None
    assert column_type.process_result_value(None, dialect) is None
