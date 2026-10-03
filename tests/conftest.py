import io
import json
import os
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from vision_hub.core.config import ENV_PREFIX, AppConfig, Environment, Settings, get_settings
from vision_hub.core.logging import configure_logging
from vision_hub.main import create_app

type SettingsFactory = Callable[..., Settings]
type LogRecords = Callable[[], list[dict[str, Any]]]


@pytest.fixture(autouse=True)
def isolated_settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Keep the developer's shell variables and local `.env` out of every test."""
    for key in list(os.environ):
        if key.startswith(ENV_PREFIX):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings_factory() -> SettingsFactory:
    """Build test settings; keyword arguments override ``AppConfig`` fields."""

    def factory(**app_overrides: Any) -> Settings:
        return Settings(app=AppConfig(env=Environment.TEST, **app_overrides))

    return factory


@pytest.fixture
def settings(settings_factory: SettingsFactory) -> Settings:
    return settings_factory()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    """HTTP client with the app's lifespan running (startup/shutdown and lifespan state)."""
    async with LifespanManager(app) as manager:
        transport = httpx2.ASGITransport(app=manager.app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


@pytest.fixture
def log_records(app: FastAPI, settings_factory: SettingsFactory) -> LogRecords:
    """Redirect logging to an in-memory JSON stream; call the result to get parsed records.

    Depends on ``app`` so it runs after ``create_app`` has done its own logging setup.
    """
    stream = io.StringIO()
    configure_logging(settings_factory(log_format="json"), stream=stream)
    return lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
