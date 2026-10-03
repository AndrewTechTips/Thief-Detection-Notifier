from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from starlette.requests import HTTPConnection

from vision_hub import __version__
from vision_hub.api.deps import ContainerDep, SettingsDep, get_container
from vision_hub.core.config import Settings
from vision_hub.main import create_app


def test_factory_uses_global_settings_when_none_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_HUB_APP__NAME", "Garage Hub")

    app = create_app()

    assert app.title == "Garage Hub"
    assert app.version == __version__


async def test_openapi_schema_is_served(client: httpx2.AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    info = response.json()["info"]
    assert (info["title"], info["version"]) == ("IoT Vision Hub", __version__)
    assert info["summary"]


async def test_dependencies_resolve_from_the_lifespan_container(
    app: FastAPI, settings: Settings, client: httpx2.AsyncClient
) -> None:
    seen: dict[str, object] = {}

    @app.get("/probe")
    async def probe(container: ContainerDep, app_settings: SettingsDep) -> dict[str, str]:
        seen.update(container=container, settings=app_settings)
        return {"ok": "yes"}

    response = await client.get("/probe")

    assert response.status_code == 200
    assert seen["settings"] is settings
    assert seen["settings"] is getattr(seen["container"], "settings", None)


async def test_lifespan_logs_startup_and_shutdown(
    app: FastAPI, log_records: Callable[[], list[dict[str, Any]]]
) -> None:
    async with LifespanManager(app):
        pass

    events = [record["event"] for record in log_records()]
    assert events == ["startup", "container_started", "container_stopped", "shutdown"]


def test_container_is_unavailable_before_lifespan() -> None:
    connection = HTTPConnection({"type": "http", "state": {}})

    with pytest.raises(RuntimeError, match="lifespan has not started"):
        get_container(connection)
