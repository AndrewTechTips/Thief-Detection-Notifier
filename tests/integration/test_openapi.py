import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from vision_hub.api.openapi import openapi_document
from vision_hub.core.config import Settings
from vision_hub.core.errors import PROBLEM_JSON, NotFoundError
from vision_hub.main import create_app
from vision_hub.schemas.base import ApiSchema
from vision_hub.schemas.pagination import Page, PageParamsDep
from vision_hub.schemas.problem import ProblemDetail


class Camera(ApiSchema):
    id: str


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    @app.get(
        "/api/v1/cameras",
        tags=["cameras"],
        responses={404: {"model": ProblemDetail}},
    )
    async def list_cameras(page: PageParamsDep) -> Page[Camera]:
        raise NotFoundError

    return app


async def fetch(app: FastAPI, path: str) -> httpx2.Response:
    async with (
        LifespanManager(app) as manager,
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=manager.app), base_url="http://localhost"
        ) as http,
    ):
        return await http.get(path)


class TestDocsVisibility:
    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    async def test_enabled_by_default_outside_production(self, app: FastAPI, path: str) -> None:
        assert (await fetch(app, path)).status_code == 200

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    async def test_can_be_disabled(
        self, settings_factory: Callable[..., Settings], path: str
    ) -> None:
        app = create_app(settings_factory(docs_enabled=False))

        response = await fetch(app, path)

        assert response.status_code == 404
        assert response.headers["content-type"] == PROBLEM_JSON


class TestSchema:
    def test_operation_ids_are_readable(self, app: FastAPI) -> None:
        schema = app.openapi()

        assert schema["paths"]["/api/v1/health/live"]["get"]["operationId"] == "health_live"
        assert schema["paths"]["/api/v1/cameras"]["get"]["operationId"] == "cameras_list_cameras"

    def test_tags_are_documented(self, app: FastAPI) -> None:
        assert "health" in [tag["name"] for tag in app.openapi()["tags"]]

    def test_errors_are_documented_as_problem_details(self, app: FastAPI) -> None:
        responses = app.openapi()["paths"]["/api/v1/cameras"]["get"]["responses"]
        problem_ref = {"$ref": "#/components/schemas/ProblemDetail"}

        for status in ("404", "422", "500"):
            assert responses[status]["content"] == {PROBLEM_JSON: {"schema": problem_ref}}

    def test_every_operation_documents_500(self, app: FastAPI) -> None:
        for path_item in app.openapi()["paths"].values():
            for operation in path_item.values():
                assert "500" in operation["responses"]

    def test_fastapi_validation_schemas_are_replaced(self, app: FastAPI) -> None:
        components: dict[str, Any] = app.openapi()["components"]["schemas"]

        assert "HTTPValidationError" not in components
        assert "ValidationError" not in components
        assert {"ProblemDetail", "FieldError"} <= components.keys()

    def test_readiness_documents_503_with_its_own_body(self, app: FastAPI) -> None:
        responses = app.openapi()["paths"]["/api/v1/health/ready"]["get"]["responses"]

        assert responses["503"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/Readiness"
        }

    def test_generic_page_schema_is_generated(self, app: FastAPI) -> None:
        components = app.openapi()["components"]["schemas"]
        page = next(schema for name, schema in components.items() if name.startswith("Page"))

        assert set(page["properties"]) == {"items", "next_cursor"}

    def test_post_processing_is_idempotent(self, app: FastAPI) -> None:
        assert app.openapi() == app.openapi()

    def test_websocket_messages_are_published_as_unions(self, app: FastAPI) -> None:
        components = app.openapi()["components"]["schemas"]

        server = components["WsServerMessage"]
        assert server["discriminator"]["propertyName"] == "type"
        assert set(server["discriminator"]["mapping"]) == {
            "motion.started",
            "motion.ended",
            "device.status",
            "subscription",
            "replay.done",
            "ping",
            "error",
        }
        client = components["WsClientMessage"]
        client_types = {"subscribe", "unsubscribe", "pong", "resume"}
        assert set(client["discriminator"]["mapping"]) == client_types
        # Every reference resolves to a published component.
        refs = {
            ref.rsplit("/", 1)[1]
            for message in (server, client)
            for ref in message["discriminator"]["mapping"].values()
        }
        assert refs <= components.keys()


# The dashboard generates its TypeScript types from this file (frontend/src/api/schema.d.ts).
DASHBOARD_SCHEMA = Path(__file__).parents[2] / "frontend" / "openapi.json"


def test_dashboard_schema_is_current(settings: Settings) -> None:
    current = openapi_document(create_app(settings))

    assert json.loads(DASHBOARD_SCHEMA.read_text(encoding="utf-8")) == json.loads(current), (
        "frontend/openapi.json is out of date: run `npm --prefix frontend run api:sync`"
    )
