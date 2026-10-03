"""Run Alembic migrations programmatically. The scripts ship inside the package (so the
Docker image has them); ``alembic.ini`` at the repository root points at the same place."""

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from vision_hub.core.logging import get_logger

SCRIPT_LOCATION = "vision_hub.infra.db:migrations"

logger = get_logger(__name__)


def alembic_config(connection: Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    config.attributes["connection"] = connection
    return config


async def upgrade_to_head(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: command.upgrade(alembic_config(sync), "head"))
    logger.info("database_migrated", backend=engine.url.get_backend_name())


async def downgrade_to_base(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: command.downgrade(alembic_config(sync), "base"))
