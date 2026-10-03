from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from vision_hub.core.errors import PROBLEM_JSON, AppError, AuthenticationError, NotFoundError

type LogRecords = Callable[[], list[dict[str, Any]]]


class CameraCredentials(BaseModel):
    username: str
    password: str
    port: int


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    """The default app plus routes that raise each kind of error."""

    @app.get("/cameras/{camera_id}")
    async def get_camera(camera_id: str) -> None:
        raise NotFoundError(f"Camera {camera_id} not found", device_id=camera_id)

    @app.get("/private")
    async def private() -> None:
        raise AuthenticationError

    @app.get("/broken-domain")
    async def broken_domain() -> None:
        raise AppError("Storage backend misconfigured")

    @app.get("/http/{case}")
    async def http_error(case: str) -> None:
        details: dict[str, Any] = {
            "custom": "Camera is busy",
            "default": None,
            "structured": {"reason": "busy"},
        }
        status = 499 if case == "nonstandard" else 409
        raise HTTPException(status_code=status, detail=details.get(case))

    @app.post("/cameras")
    async def add_camera(credentials: CameraCredentials) -> None:
        return None

    return app


def assert_problem(response: httpx2.Response, status: int) -> dict[str, Any]:
    assert response.status_code == status
    assert response.headers["content-type"] == PROBLEM_JSON
    body: dict[str, Any] = response.json()
    assert body["status"] == status
    assert body["request_id"] == response.headers["X-Request-ID"]
    return body


async def test_domain_error_becomes_typed_problem(client: httpx2.AsyncClient) -> None:
    response = await client.get("/cameras/cam-7")

    body = assert_problem(response, 404)
    assert body["type"] == "urn:vision-hub:problem:not-found"
    assert body["title"] == "Resource Not Found"
    assert body["detail"] == "Camera cam-7 not found"
    assert body["instance"] == "/cameras/cam-7"
    assert body["device_id"] == "cam-7"


async def test_authentication_error_sets_www_authenticate(client: httpx2.AsyncClient) -> None:
    response = await client.get("/private")

    assert_problem(response, 401)
    assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_server_side_domain_error_is_logged_as_error(
    client: httpx2.AsyncClient, log_records: LogRecords
) -> None:
    response = await client.get("/broken-domain")

    assert_problem(response, 500)
    [record] = [r for r in log_records() if r["event"] == "app_error"]
    assert record["level"] == "error"
    assert record["code"] == "internal-error"


@pytest.mark.parametrize(
    ("case", "status", "detail"),
    [
        ("custom", 409, "Camera is busy"),
        ("default", 409, None),  # Starlette's default detail is just the reason phrase
        ("structured", 409, None),  # non-string detail must not break the response
        ("nonstandard", 499, None),
    ],
)
async def test_http_exceptions_become_problems(
    client: httpx2.AsyncClient, case: str, status: int, detail: str | None
) -> None:
    response = await client.get(f"/http/{case}")

    body = assert_problem(response, status)
    assert body["type"] == "about:blank"
    assert body.get("detail") == detail


async def test_unknown_route_is_a_problem(client: httpx2.AsyncClient) -> None:
    body = assert_problem(await client.get("/does-not-exist"), 404)

    assert body["title"] == "Not Found"


async def test_wrong_method_keeps_allow_header(client: httpx2.AsyncClient) -> None:
    response = await client.delete("/private")

    assert_problem(response, 405)
    assert response.headers["Allow"] == "GET"


async def test_validation_errors_list_fields_without_echoing_input(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.post(
        "/cameras", json={"username": "admin", "password": "hunter2-secret", "port": "abc"}
    )

    body = assert_problem(response, 422)
    assert body["title"] == "Validation Failed"
    assert body["errors"] == [
        {
            "loc": ["body", "port"],
            "msg": "Input should be a valid integer, unable to parse string as an integer",
            "type": "int_parsing",
        }
    ]
    assert "hunter2-secret" not in response.text
    assert "abc" not in response.text
